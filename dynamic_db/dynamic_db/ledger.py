"""The table ledger: what happened in the database, table by table, over time.

WHY THIS EXISTS BESIDE THE CHANGE DETECTOR
------------------------------------------
:mod:`dynamic_db.changes` answers one question -- does anything a compiled
capability depends on differ from the last run? -- and then its baseline is
overwritten. That is exactly right for deciding what to recompile, and it
leaves three things unanswered:

* **what happened earlier.** One baseline has no past. The ledger keeps an
  append-only log of every table and column change it has seen, with dates.
* **what arrived outside the allowlist.** New tables are this database's most
  common change, and a table in a schema INCLUDED_TABLES never reaches was
  invisible. The ledger watches a wider set of schemas (WATCH_SCHEMAS).
* **what a new table probably is.** A ``TABLE_ADDED`` was reported and then
  ignored. The ledger triages it -- a possible successor of an approved table,
  an extension that references one, a member of a table family, noise -- and
  flags the likely-relevant ones for a person to review.

OBSERVE WIDELY, APPROVE NARROWLY
--------------------------------
Only catalogue metadata is read: table and column names, types, nullability,
creation/modification dates and foreign keys. No row of any table is ever
selected, and nothing here feeds a prompt or compiled SQL. A new table is never
approved by this module -- ``status`` is advisory, and only a person editing
INCLUDED_TABLES changes what the rest of DYNAMIC_DB can see.

READ-ONLY BY CONSTRUCTION
-------------------------
Every statement is a SELECT against ``sys.*`` catalogue views, passed through
:func:`dynamic_db.db.assert_read_only` before it runs, on the same read-only
connection (``ApplicationIntent=ReadOnly``, ``readonly=True``) the rest of the
package uses. Nothing is created, altered or written in the database: the
registry and the change log live in this database's own cache directory
(``table_registry.json`` and ``table_changes.jsonl``), next to the other
artefacts DYNAMIC_DB keeps.

``sys.tables.create_date`` and ``modify_date`` do two jobs. They make a pass
cheap -- the column and foreign-key catalogues are read only when some table
was created, altered or dropped since the last pass -- and they reach back
before tracking began: the registry records when SQL Server says each table was
created, so even the very first pass says something about the past.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dynamic_db import identity
from dynamic_db.config import get_settings

logger = logging.getLogger(__name__)

# Statuses. Stable strings: they are persisted and filtered on.
STATUS_APPROVED = "approved"    # in INCLUDED_TABLES
STATUS_CANDIDATE = "candidate"  # probably relevant -- worth a person's review
STATUS_OBSERVED = "observed"    # seen, nothing suggests it matters yet
STATUS_IGNORED = "ignored"      # matches WATCH_IGNORE_PATTERNS
STATUS_REMOVED = "removed"      # no longer in the database

# What a table probably is, beside its status.
KIND_APPROVED = "approved"
KIND_SUCCESSOR = "possible_successor"
KIND_EXTENSION = "extension"
KIND_FAMILY = "family_member"
KIND_NOISE = "noise"
KIND_UNRELATED = "unrelated"

# Change-log event types.
EVENT_TRACKING_STARTED = "TRACKING_STARTED"
EVENT_TABLE_ADDED = "TABLE_ADDED"
EVENT_TABLE_REMOVED = "TABLE_REMOVED"
EVENT_TABLE_REAPPEARED = "TABLE_REAPPEARED"
EVENT_COLUMN_ADDED = "COLUMN_ADDED"
EVENT_COLUMN_REMOVED = "COLUMN_REMOVED"
EVENT_COLUMN_CHANGED = "COLUMN_CHANGED"
EVENT_REFERENCES_CHANGED = "REFERENCES_CHANGED"
EVENT_TABLE_APPROVED = "TABLE_APPROVED"
EVENT_TABLE_UNAPPROVED = "TABLE_UNAPPROVED"

_REGISTRY_STEM = "table_registry"
_EVENTS_FILE = "table_changes.jsonl"
_REGISTRY_VERSION = 1

#: Fewer shared columns than this never make two tables "alike", whatever the
#: ratio: two three-column lookup tables sharing "id" and "name" are not
#: versions of one another.
_MIN_SHARED_COLUMNS = 3

#: Suffixes that make one table a version, period or copy of another --
#: ``task_daily_v2``, ``tasks_2026``, ``activity_new``. Stripped repeatedly to
#: find a table's family name.
_FAMILY_SUFFIX = re.compile(
    r"(?:_?v\d+|_new|_old|_copy|_backup|_bak|_archive|_hist(?:ory)?|_?\d{4}(?:_?\d{2}){0,2}|_\d+)$"
)

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# What one pass reads
# ---------------------------------------------------------------------------
@dataclass
class TableMeta:
    """Catalogue metadata for one table. Never any data."""

    name: str  # "schema.table", as SQL Server spells it
    create_date: Optional[str] = None
    modify_date: Optional[str] = None
    #: column name -> "type(max_length) NULL|NOT NULL"; None when this pass did
    #: not need to read the column catalogue for this table.
    columns: Optional[Dict[str, str]] = None
    #: "schema.table" names this table references by foreign key.
    references: Optional[List[str]] = None

    def key(self) -> str:
        return self.name.lower()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


def _schema_filter(schemas: Sequence[str], column: str) -> Tuple[str, List[str]]:
    """A WHERE fragment restricting ``column`` to ``schemas``, or none for ``*``."""
    if not schemas or "*" in schemas:
        return "", []
    placeholders = ", ".join("?" for _ in schemas)
    return f" WHERE LOWER({column}) IN ({placeholders})", list(schemas)


def _select(cur, sql: str, params: Sequence[Any] = ()) -> List[tuple]:
    """Run one catalogue SELECT, after proving it is one."""
    from dynamic_db.db import assert_read_only

    assert_read_only(sql)
    cur.execute(sql, *params)
    return cur.fetchall()


def read_catalog(
    schemas: Sequence[str],
    *,
    known: Optional[Dict[str, Dict[str, Any]]] = None,
    full: bool = False,
) -> Dict[str, TableMeta]:
    """Read the watched schemas' catalogue. Metadata only, read-only.

    ``known`` is the registry's current entries. When every table's creation
    and modification dates still match them and no table has appeared or gone,
    the column and foreign-key catalogues are not read at all -- nothing can
    have changed in them. ``full`` reads them regardless.
    """
    from dynamic_db.db import get_connection
    from dynamic_db.introspect import _is_excluded_column, _is_secret

    known = known or {}
    with get_connection() as connection:
        cur = connection.cursor()

        where, params = _schema_filter(schemas, "s.name")
        rows = _select(
            cur,
            "SELECT s.name, t.name, t.create_date, t.modify_date "
            "FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id"
            + where,
            params,
        )
        tables: Dict[str, TableMeta] = {}
        for schema_name, table_name, create_date, modify_date in rows:
            meta = TableMeta(
                name=f"{schema_name}.{table_name}",
                create_date=_iso(create_date),
                modify_date=_iso(modify_date),
            )
            tables[meta.key()] = meta

        live_known = {k for k, v in known.items() if v.get("status") != STATUS_REMOVED}
        moved = full or set(tables) != live_known or any(
            known[key].get("modify_date") != meta.modify_date
            or known[key].get("create_date") != meta.create_date
            or known[key].get("columns") is None
            for key, meta in tables.items()
        )
        if not moved:
            return tables

        where, params = _schema_filter(schemas, "s.name")
        for schema_name, table_name, column, type_name, max_length, nullable in _select(
            cur,
            "SELECT s.name, t.name, c.name, ty.name, c.max_length, c.is_nullable "
            "FROM sys.columns c "
            "JOIN sys.tables t ON t.object_id = c.object_id "
            "JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "JOIN sys.types ty ON ty.user_type_id = c.user_type_id"
            + where,
            params,
        ):
            meta = tables.get(f"{schema_name}.{table_name}".lower())
            if meta is None or _is_secret(column) or _is_excluded_column(schema_name, table_name, column):
                continue
            if meta.columns is None:
                meta.columns = {}
            length = "" if max_length is None else f"({max_length})"
            meta.columns[column] = f"{type_name}{length} {'NULL' if nullable else 'NOT NULL'}"

        where, params = _schema_filter(schemas, "ps.name")
        for parent_schema, parent_table, ref_schema, ref_table in _select(
            cur,
            "SELECT ps.name, pt.name, rs.name, rt.name "
            "FROM sys.foreign_keys fk "
            "JOIN sys.tables pt ON pt.object_id = fk.parent_object_id "
            "JOIN sys.schemas ps ON ps.schema_id = pt.schema_id "
            "JOIN sys.tables rt ON rt.object_id = fk.referenced_object_id "
            "JOIN sys.schemas rs ON rs.schema_id = rt.schema_id"
            + where,
            params,
        ):
            meta = tables.get(f"{parent_schema}.{parent_table}".lower())
            if meta is None:
                continue
            if meta.references is None:
                meta.references = []
            reference = f"{ref_schema}.{ref_table}"
            if reference not in meta.references:
                meta.references.append(reference)

        for meta in tables.values():
            if meta.columns is None:
                meta.columns = {}
            meta.references = sorted(meta.references or [], key=str.lower)
    return tables


# ---------------------------------------------------------------------------
# Triage: what is a table probably, and does it deserve a look?
# ---------------------------------------------------------------------------
def family_name(table_key: str) -> str:
    """A table's name with version / period / copy suffixes stripped."""
    base = table_key.split(".", 1)[-1].lower()
    while True:
        stripped = _FAMILY_SUFFIX.sub("", base)
        if stripped == base or not stripped:
            return base
        base = stripped


def is_ignored(table_key: str, patterns: Iterable[str]) -> bool:
    bare = table_key.split(".", 1)[-1]
    return any(
        fnmatch.fnmatchcase(table_key, pattern) or fnmatch.fnmatchcase(bare, pattern)
        for pattern in patterns
    )


def column_overlap(a: Iterable[str], b: Iterable[str]) -> Tuple[float, int]:
    """(shared / all, shared) over lower-cased column names."""
    left = {name.lower() for name in a}
    right = {name.lower() for name in b}
    union = left | right
    shared = len(left & right)
    return (shared / len(union) if union else 0.0), shared


def triage(
    key: str,
    entry: Dict[str, Any],
    entries: Dict[str, Dict[str, Any]],
    *,
    approved: Iterable[str],
    ignore_patterns: Iterable[str],
    similarity_threshold: float,
) -> Tuple[str, str, List[str], str]:
    """``(status, kind, related_to, note)`` for one live table.

    Deterministic, and advisory only: a "candidate" is a table a person should
    look at, never one anything here adopts.
    """
    approved = {name.lower() for name in approved}
    if key in approved:
        return STATUS_APPROVED, KIND_APPROVED, [], "in INCLUDED_TABLES"
    if is_ignored(key, ignore_patterns):
        return STATUS_IGNORED, KIND_NOISE, [], "matches WATCH_IGNORE_PATTERNS"

    live = {k: v for k, v in entries.items() if v.get("status") != STATUS_REMOVED}
    approved_entries = {k: v for k, v in entries.items() if k in approved}
    columns = entry.get("columns") or {}
    family = family_name(key)

    # 1. A new version, or a replacement, of an approved table -- including
    #    one that has since gone, which is exactly when its successor matters.
    successors: List[Tuple[float, str, str]] = []
    for other_key, other in approved_entries.items():
        ratio, shared = column_overlap(columns, other.get("columns") or {})
        same_family = family and family == family_name(other_key)
        if same_family or (shared >= _MIN_SHARED_COLUMNS and ratio >= similarity_threshold):
            reason = (
                f"same name family as {other['name']}"
                if same_family
                else f"{shared} shared columns ({ratio:.0%}) with {other['name']}"
            )
            successors.append((ratio + (1 if same_family else 0), other["name"], reason))
    if successors:
        successors.sort(reverse=True)
        return (
            STATUS_CANDIDATE,
            KIND_SUCCESSOR,
            [name for _, name, _ in successors],
            "possible successor: " + "; ".join(reason for _, _, reason in successors),
        )

    # 2. It hangs off an approved table by foreign key, or one hangs off it.
    references = {name.lower() for name in entry.get("references") or []}
    linked = sorted(
        other["name"]
        for other_key, other in approved_entries.items()
        if other_key in references
        or key in {name.lower() for name in other.get("references") or []}
    )
    if linked:
        return (
            STATUS_CANDIDATE,
            KIND_EXTENSION,
            linked,
            "related by foreign key to " + ", ".join(linked),
        )

    # 3. One of a family of tables (tasks_2025, tasks_2026, ...), none approved.
    siblings = sorted(
        other["name"]
        for other_key, other in live.items()
        if other_key != key and family and family_name(other_key) == family
    )
    if siblings:
        return STATUS_OBSERVED, KIND_FAMILY, siblings, "same name family as " + ", ".join(siblings)

    return STATUS_OBSERVED, KIND_UNRELATED, [], ""


# ---------------------------------------------------------------------------
# Reconciling one pass against the registry -- pure, no I/O
# ---------------------------------------------------------------------------
def _event(at: str, kind: str, table: str, **fields: Any) -> Dict[str, Any]:
    event = {"at": at, "type": kind, "table": table}
    event.update({k: v for k, v in fields.items() if v is not None})
    return event


def reconcile(
    registry: Dict[str, Any],
    catalog: Dict[str, TableMeta],
    *,
    approved: Iterable[str],
    ignore_patterns: Iterable[str],
    similarity_threshold: float,
    database: str = "",
    now: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """The registry after this pass, and the events that describe the change.

    The first pass records a baseline and a single ``TRACKING_STARTED`` event
    rather than one ``TABLE_ADDED`` per existing table: those tables were not
    added now, and SQL Server's own ``create_date`` -- kept on every entry --
    already says when they were.
    """
    now = now or _now()
    approved = [name.lower() for name in approved]
    previous: Dict[str, Dict[str, Any]] = dict(registry.get("tables") or {})
    first_run = not previous
    events: List[Dict[str, Any]] = []
    entries: Dict[str, Dict[str, Any]] = {}

    for key, meta in catalog.items():
        before = previous.get(key)
        entry = dict(before) if before else {"name": meta.name, "first_seen": now}
        entry.update(
            name=meta.name,
            create_date=meta.create_date,
            modify_date=meta.modify_date,
            last_seen=now,
        )
        entry.pop("removed_at", None)
        if meta.columns is not None:
            entry["columns"] = dict(meta.columns)
        if meta.references is not None:
            entry["references"] = list(meta.references)
        entry.setdefault("columns", None)
        entry.setdefault("references", [])
        entry.setdefault("last_changed", meta.modify_date or now)

        if before is None and not first_run:
            events.append(_event(now, EVENT_TABLE_ADDED, meta.name, created=meta.create_date))
            entry["last_changed"] = now
        elif before is not None and before.get("status") == STATUS_REMOVED:
            events.append(_event(now, EVENT_TABLE_REAPPEARED, meta.name, created=meta.create_date))
            entry["last_changed"] = now
        elif before is not None and meta.columns is not None and before.get("columns") is not None:
            changed = _column_events(now, meta.name, before["columns"], meta.columns)
            if meta.references is not None and sorted(before.get("references") or []) != meta.references:
                changed.append(
                    _event(
                        now, EVENT_REFERENCES_CHANGED, meta.name,
                        before=", ".join(before.get("references") or []) or "(none)",
                        after=", ".join(meta.references) or "(none)",
                    )
                )
            if changed:
                events.extend(changed)
                entry["last_changed"] = now
        entry["signature"] = _signature(entry.get("columns") or {})
        entries[key] = entry

    for key, before in previous.items():
        if key in catalog:
            continue
        entry = dict(before)
        if before.get("status") != STATUS_REMOVED:
            events.append(
                _event(now, EVENT_TABLE_REMOVED, before["name"], was=before.get("status"))
            )
            entry.update(status=STATUS_REMOVED, removed_at=now, last_changed=now)
        entries[key] = entry

    # Status after everything is known, so triage sees this pass's tables.
    for key, entry in entries.items():
        if entry.get("status") == STATUS_REMOVED and key not in catalog:
            continue
        old_status = (previous.get(key) or {}).get("status")
        status, kind, related, note = triage(
            key, entry, entries,
            approved=approved,
            ignore_patterns=ignore_patterns,
            similarity_threshold=similarity_threshold,
        )
        entry.update(status=status, kind=kind, related_to=related, note=note)
        if not first_run and old_status and old_status != STATUS_REMOVED:
            if status == STATUS_APPROVED and old_status != STATUS_APPROVED:
                events.append(_event(now, EVENT_TABLE_APPROVED, entry["name"], was=old_status))
            elif old_status == STATUS_APPROVED and status != STATUS_APPROVED:
                events.append(_event(now, EVENT_TABLE_UNAPPROVED, entry["name"], now_status=status))

    # Say what each new or returning table was triaged as, on its own event.
    for event in events:
        if event["type"] in (EVENT_TABLE_ADDED, EVENT_TABLE_REAPPEARED):
            entry = entries[event["table"].lower()]
            event.update(status=entry["status"], kind=entry["kind"])
            if entry.get("note"):
                event["note"] = entry["note"]
        elif event["type"] == EVENT_TABLE_REMOVED and event.get("was") == STATUS_APPROVED:
            successors = sorted(
                other["name"]
                for other in entries.values()
                if other.get("status") == STATUS_CANDIDATE
                and event["table"] in (other.get("related_to") or [])
            )
            if successors:
                event["note"] = "possible successor(s): " + ", ".join(successors)

    if first_run:
        events.insert(
            0,
            _event(
                now, EVENT_TRACKING_STARTED, "*",
                note=f"{len(catalog)} table(s) recorded as the baseline",
            ),
        )

    updated = {
        "version": _REGISTRY_VERSION,
        "database": database or registry.get("database", ""),
        "started_at": registry.get("started_at") or now,
        "last_checked": now,
        "last_changed": now if events else registry.get("last_changed", now),
        "tables": dict(sorted(entries.items())),
    }
    return updated, events


def _column_events(
    now: str, table: str, before: Dict[str, str], after: Dict[str, str]
) -> List[Dict[str, Any]]:
    old = {name.lower(): (name, text) for name, text in before.items()}
    new = {name.lower(): (name, text) for name, text in after.items()}
    events: List[Dict[str, Any]] = []
    for low in sorted(set(new) - set(old)):
        events.append(_event(now, EVENT_COLUMN_ADDED, table, column=new[low][0], after=new[low][1]))
    for low in sorted(set(old) - set(new)):
        events.append(_event(now, EVENT_COLUMN_REMOVED, table, column=old[low][0], before=old[low][1]))
    for low in sorted(set(old) & set(new)):
        if old[low][1].lower() != new[low][1].lower():
            events.append(
                _event(
                    now, EVENT_COLUMN_CHANGED, table,
                    column=new[low][0], before=old[low][1], after=new[low][1],
                )
            )
    return events


def _signature(columns: Dict[str, str]) -> str:
    h = hashlib.sha256()
    for name in sorted(columns, key=str.lower):
        h.update(f"{name.lower()}|{columns[name].lower()}\n".encode("utf-8"))
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Persistence -- local files only, never the database
# ---------------------------------------------------------------------------
def load_registry() -> Dict[str, Any]:
    raw = identity.read_cache(_REGISTRY_STEM, "json")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("dynamic: table registry is unreadable; starting a new baseline")
        return {}
    return data if isinstance(data, dict) else {}


def _save_registry(registry: Dict[str, Any]) -> None:
    identity.write_cache(_REGISTRY_STEM, "json", json.dumps(registry, indent=2, sort_keys=True))


def _append_events(events: List[Dict[str, Any]]) -> None:
    """Append-only: an event, once written, is never rewritten or removed."""
    if not events:
        return
    path = identity.cache_dir() / _EVENTS_FILE
    try:
        with path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")
    except OSError:  # noqa: BLE001 - history must never break a pass
        logger.warning("dynamic: could not append to %s", path)


def load_events(
    *, table: Optional[str] = None, since: Optional[str] = None, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """The change log, oldest first; filtered by table name and ISO date."""
    path = identity.cache_dir() / _EVENTS_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    events: List[Dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if table and table.lower() not in (event.get("table") or "").lower():
            continue
        if since and (event.get("at") or "") < since:
            continue
        events.append(event)
    return events[-limit:] if limit else events


# ---------------------------------------------------------------------------
# One pass, and the watcher that repeats it
# ---------------------------------------------------------------------------
def observe(*, full: bool = False) -> Dict[str, Any]:
    """Read the catalogue, reconcile it, record what changed. Read-only.

    Returns a summary with this pass's events. Raises
    :class:`dynamic_db.db.DatabaseUnavailable` when the database cannot be
    read; the registry and log are then left exactly as they were.
    """
    settings = get_settings()
    schemas = settings.watched_schemas
    with _lock:
        registry = load_registry()
        catalog = read_catalog(schemas, known=registry.get("tables") or {}, full=full)
        updated, events = reconcile(
            registry,
            catalog,
            approved=settings.included_tables,
            ignore_patterns=settings.watch_ignore_patterns,
            similarity_threshold=settings.watch_similarity_threshold,
            database=identity.database_identity(),
        )
        _save_registry(updated)
        _append_events(events)

    for event in events:
        if event["type"] == EVENT_TRACKING_STARTED:
            logger.info("dynamic: table ledger started - %s", event.get("note", ""))
        elif event.get("status") == STATUS_CANDIDATE or event["type"] in (
            EVENT_TABLE_REMOVED, EVENT_TABLE_UNAPPROVED,
        ):
            logger.warning("dynamic: table ledger - %s %s %s", event["type"], event["table"], event.get("note", ""))
        else:
            logger.info("dynamic: table ledger - %s %s", event["type"], event["table"])
    return {**summary(updated), "schemas": schemas, "events": events}


def summary(registry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Counts by status, the tables awaiting review, and when it last ran."""
    registry = load_registry() if registry is None else registry
    tables = registry.get("tables") or {}
    counts: Dict[str, int] = {}
    for entry in tables.values():
        counts[entry.get("status", "?")] = counts.get(entry.get("status", "?"), 0) + 1
    return {
        "database": registry.get("database", ""),
        "tracking_since": registry.get("started_at"),
        "last_checked": registry.get("last_checked"),
        "last_changed": registry.get("last_changed"),
        "counts": counts,
        "candidates": [
            {
                "table": entry["name"],
                "kind": entry.get("kind"),
                "related_to": entry.get("related_to") or [],
                "note": entry.get("note", ""),
                "first_seen": entry.get("first_seen"),
                "created": entry.get("create_date"),
            }
            for entry in tables.values()
            if entry.get("status") == STATUS_CANDIDATE
        ],
    }


_watcher: Optional[threading.Thread] = None
_watcher_stop = threading.Event()


def start_watcher(interval_minutes: Optional[int] = None) -> bool:
    """Run :func:`observe` now and then every ``interval_minutes`` in a
    daemon thread. Returns False when disabled (interval 0) or already running.
    A failed pass is logged and retried next interval -- never raised."""
    global _watcher
    interval = get_settings().watch_interval_minutes if interval_minutes is None else interval_minutes
    if interval <= 0:
        logger.info("dynamic: table watcher disabled (WATCH_INTERVAL_MINUTES=0)")
        return False
    with _lock:
        if _watcher is not None and _watcher.is_alive():
            return False
        _watcher_stop.clear()

        def _loop() -> None:
            while not _watcher_stop.is_set():
                try:
                    observe()
                except Exception:  # noqa: BLE001 - a background thread must never die
                    logger.warning("dynamic: table ledger pass failed", exc_info=True)
                _watcher_stop.wait(interval * 60)

        _watcher = threading.Thread(target=_loop, name="dynamic-db-table-watcher", daemon=True)
        _watcher.start()
    return True


def stop_watcher() -> None:
    _watcher_stop.set()
