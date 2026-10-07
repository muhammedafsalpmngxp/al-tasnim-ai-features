"""What moved in the database since last time, stated as facts an operator can read.

Deterministic. No model is involved and none is needed: a column's datatype
either changed or it did not.

WHY THE REPORT IS STRUCTURED RATHER THAN A LOG LINE
---------------------------------------------------
Two different readers consume it. The scheduler intersects the changed objects
with each capability's recorded dependencies to decide what -- if anything --
has to be recompiled; a person reads it to understand why a recompile happened
at all. A report that only said "the schema changed" would force a full
recompile of everything and explain nothing.

ROW COUNTS ARE NOT A CHANGE. A new day of task data arrives every morning. If
that counted, every capability would be recompiled daily at full reasoning-model
cost, which is precisely what the structure/live fingerprint split exists to
prevent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from dynamic_db.introspect import SchemaSnapshot, TableFact

# Change kinds. Stable strings: they end up in a persisted report and in tests.
TABLE_ADDED = "TABLE_ADDED"
TABLE_REMOVED = "TABLE_REMOVED"
COLUMN_ADDED = "COLUMN_ADDED"
COLUMN_REMOVED = "COLUMN_REMOVED"
COLUMN_DATATYPE_CHANGED = "COLUMN_DATATYPE_CHANGED"
COLUMN_NULLABILITY_CHANGED = "COLUMN_NULLABILITY_CHANGED"
PRIMARY_KEY_CHANGED = "PRIMARY_KEY_CHANGED"
FOREIGN_KEYS_CHANGED = "FOREIGN_KEYS_CHANGED"
CONSTRAINTS_CHANGED = "CONSTRAINTS_CHANGED"

#: Changes that can only ADD capability, never take it away. A query that
#: worked before an unused column appeared still works after it. Tracked and
#: reported, but they never on their own mark a capability stale.
NON_BREAKING = frozenset({TABLE_ADDED, COLUMN_ADDED})


@dataclass
class SchemaChange:
    type: str
    object: str
    before: Optional[str] = None
    after: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SchemaChangeReport:
    changed: bool = False
    structure_fingerprint_old: str = ""
    structure_fingerprint_new: str = ""
    changes: List[SchemaChange] = field(default_factory=list)
    affected_capabilities: List[str] = field(default_factory=list)
    first_run: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "changed": self.changed,
            "first_run": self.first_run,
            "structure_fingerprint_old": self.structure_fingerprint_old,
            "structure_fingerprint_new": self.structure_fingerprint_new,
            "changes": [change.as_dict() for change in self.changes],
            "affected_capabilities": list(self.affected_capabilities),
        }

    def breaking_changes(self) -> List[SchemaChange]:
        return [change for change in self.changes if change.type not in NON_BREAKING]

    def summary(self) -> str:
        if self.first_run:
            return "first run against this database - nothing to compare against"
        if not self.changed:
            return "no structural change"
        counts: Dict[str, int] = {}
        for change in self.changes:
            counts[change.type] = counts.get(change.type, 0) + 1
        return ", ".join(f"{count} x {kind}" for kind, count in sorted(counts.items()))


def _table_key(object_name: str) -> str:
    """The "schema.table" part of a changed object's name.

    An object is either "schema.table" or "schema.table.column"; the table is
    the first two segments either way.
    """
    parts = object_name.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}".lower()
    return object_name.lower()


def _compare_table(previous: TableFact, current: TableFact) -> List[SchemaChange]:
    changes: List[SchemaChange] = []
    table = current.name

    old_columns = {name.lower(): col for name, col in previous.columns.items()}
    new_columns = {name.lower(): col for name, col in current.columns.items()}

    for name in sorted(set(new_columns) - set(old_columns)):
        changes.append(
            SchemaChange(COLUMN_ADDED, f"{table}.{new_columns[name].name}", None,
                         new_columns[name].type_text())
        )
    for name in sorted(set(old_columns) - set(new_columns)):
        changes.append(
            SchemaChange(COLUMN_REMOVED, f"{table}.{old_columns[name].name}",
                         old_columns[name].type_text(), None)
        )
    for name in sorted(set(old_columns) & set(new_columns)):
        before, after = old_columns[name], new_columns[name]
        if before.data_type.lower() != after.data_type.lower() or before.max_length != after.max_length:
            changes.append(
                SchemaChange(
                    COLUMN_DATATYPE_CHANGED,
                    f"{table}.{after.name}",
                    before.type_text(),
                    after.type_text(),
                )
            )
        elif before.nullable != after.nullable:
            changes.append(
                SchemaChange(
                    COLUMN_NULLABILITY_CHANGED,
                    f"{table}.{after.name}",
                    "NULL" if before.nullable else "NOT NULL",
                    "NULL" if after.nullable else "NOT NULL",
                )
            )

    if sorted(n.lower() for n in previous.primary_key) != sorted(
        n.lower() for n in current.primary_key
    ):
        changes.append(
            SchemaChange(
                PRIMARY_KEY_CHANGED,
                table,
                ", ".join(previous.primary_key) or "(none)",
                ", ".join(current.primary_key) or "(none)",
            )
        )
    if sorted(r.lower() for r in previous.foreign_keys) != sorted(
        r.lower() for r in current.foreign_keys
    ):
        changes.append(
            SchemaChange(
                FOREIGN_KEYS_CHANGED,
                table,
                "; ".join(sorted(previous.foreign_keys)) or "(none)",
                "; ".join(sorted(current.foreign_keys)) or "(none)",
            )
        )
    if sorted(c.lower() for c in previous.constraints) != sorted(
        c.lower() for c in current.constraints
    ):
        changes.append(
            SchemaChange(
                CONSTRAINTS_CHANGED,
                table,
                f"{len(previous.constraints)} constraint(s)",
                f"{len(current.constraints)} constraint(s)",
            )
        )
    return changes


def compare(
    previous: Optional[SchemaSnapshot], current: SchemaSnapshot
) -> SchemaChangeReport:
    """Everything structural that differs between two snapshots.

    Both the approved tables and the tables merely visible inside the discovery
    boundary are compared: a replacement for a table that vanished can only be
    recognised if its arrival was noticed.
    """
    if previous is None:
        return SchemaChangeReport(
            changed=False,
            first_run=True,
            structure_fingerprint_old="",
            structure_fingerprint_new=current.structure_fingerprint(),
        )

    report = SchemaChangeReport(
        structure_fingerprint_old=previous.structure_fingerprint(),
        structure_fingerprint_new=current.structure_fingerprint(),
    )

    old_keys, new_keys = set(previous.tables), set(current.tables)
    for key in sorted(new_keys - old_keys):
        report.changes.append(SchemaChange(TABLE_ADDED, current.tables[key].name))
    for key in sorted(old_keys - new_keys):
        report.changes.append(SchemaChange(TABLE_REMOVED, previous.tables[key].name))
    for key in sorted(old_keys & new_keys):
        report.changes.extend(_compare_table(previous.tables[key], current.tables[key]))

    report.changed = bool(report.changes)
    return report


def changed_objects(report: SchemaChangeReport) -> Tuple[Set[str], Set[Tuple[str, str]]]:
    """The changed tables and (table, column) pairs, for dependency intersection.

    Only BREAKING changes are returned. An added table or column cannot make
    working SQL stop working, and treating it as though it could would charge a
    recompile to every capability whenever anyone adds a column anywhere.
    """
    tables: Set[str] = set()
    columns: Set[Tuple[str, str]] = set()
    for change in report.breaking_changes():
        parts = change.object.split(".")
        if len(parts) >= 3:
            table = f"{parts[0]}.{parts[1]}".lower()
            columns.add((table, ".".join(parts[2:]).lower()))
            # The table is NOT added here: a column change must only reach the
            # capabilities that actually read that column. Adding the table
            # would make every column change a whole-table change and undo the
            # entire point of per-column dependency tracking.
        else:
            tables.add(_table_key(change.object))
    return tables, columns


def describe(report: SchemaChangeReport, limit: int = 12) -> List[str]:
    """Human-readable change lines, bounded. Never includes a value."""
    lines: List[str] = []
    for change in report.changes[:limit]:
        if change.before is None and change.after is None:
            lines.append(f"{change.type}: {change.object}")
        else:
            lines.append(
                f"{change.type}: {change.object} ({change.before or '-'} -> {change.after or '-'})"
            )
    remaining = len(report.changes) - len(lines)
    if remaining > 0:
        lines.append(f"... and {remaining} more")
    return lines


def replacement_candidates(
    missing_table: str, current: SchemaSnapshot, expected_columns: Sequence[str]
) -> List[str]:
    """Tables inside the discovery boundary that COULD stand in for a lost one.

    Evidence, never a decision. A candidate is offered to the SQL author as
    something to consider against the rules, and nothing is adopted without the
    validator and the verifier agreeing -- because two tables sharing a name
    shape prove nothing at all about whether they mean the same thing.
    """
    wanted = {c.lower() for c in expected_columns if c}
    if not wanted:
        return []
    discovery = {s.lower() for s in current.discovery_schemas}
    scored: List[Tuple[float, str]] = []
    for key, fact in current.tables.items():
        if key == missing_table.lower():
            continue
        schema = key.split(".", 1)[0]
        if discovery and schema not in discovery:
            continue
        have = {c.lower() for c in fact.columns}
        overlap = len(wanted & have)
        if not overlap:
            continue
        # Share of the columns the lost table was read for that this one also
        # has. A structural signal only; it establishes nothing semantic.
        scored.append((overlap / len(wanted), fact.name))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [f"{name} (shares {share:.0%} of the expected column names)" for share, name in scored[:5]]
