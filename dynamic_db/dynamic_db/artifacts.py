"""Compiled SQL, versioned, with a promotion step between "produced" and "in use".

THE LAST KNOWN-GOOD ARTIFACT IS NEVER OVERWRITTEN BY AN UNVERIFIED ONE. A
candidate is written to its own slot, validated, executed and verified, and
only then promoted. If any of that fails, the previous artifact is still
sitting there unchanged, and an operator can read both and see exactly what the
new one tried to do.

"STILL THERE" IS NOT THE SAME AS "STILL USABLE". A schema change that removed a
column the current artifact reads makes it wrong, not merely old, and
continuing to serve it would be the worst outcome available: a query that runs
against a changed database and quietly answers a different question. So
staleness is a DELIBERATE DETERMINISTIC DECISION taken here -- from the
artifact's own recorded dependencies against the live schema -- and never an
accident of whether a recompile happened to succeed.

WHAT MAKES AN ARTIFACT'S IDENTITY
---------------------------------
The capability, the structure of exactly the objects it reads, the compiler and
prompt versions, and the text of the rules it was compiled against. Not the row
counts -- a new day of data must never retire an artifact -- and not how the
schema was printed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dynamic_db import identity
from dynamic_db.capabilities import Capability
from dynamic_db.dependencies import Dependencies
from dynamic_db.introspect import SchemaSnapshot

logger = logging.getLogger(__name__)

# Artifact provenance.
ORIGIN_SEED = "seed"  # the human-authored baseline, registered as-is
ORIGIN_COMPILED = "compiled"  # authored by the reasoning model and verified

# Verifier outcomes.
VERIFIED_APPROVED = "approved"
VERIFIED_SEED = "seed-baseline"  # validated deterministically, never model-reviewed
VERIFIED_NOT_APPLICABLE = "not_applicable"
VERIFIED_PENDING = "pending"

#: How many superseded artifacts are kept per capability. Enough to see what
#: changed and why; not an archive.
_HISTORY_LIMIT = 10


@dataclass
class Artifact:
    capability_id: str
    sql: str
    origin: str
    version: int = 1
    generated_at: str = ""
    #: Whole-database structure fingerprint at compile time. Context for an
    #: operator; NOT what staleness is decided on -- see ``dependency_fingerprint``.
    schema_fingerprint: str = ""
    #: The fingerprint of exactly the objects this SQL reads. This is the one
    #: that decides whether the artifact is still valid.
    dependency_fingerprint: str = ""
    dependencies: Dict[str, Any] = field(default_factory=dict)
    validator_status: str = ""
    verifier_status: str = VERIFIED_PENDING
    verifier_note: str = ""
    not_applicable_reason: str = ""
    model_used: str = ""
    prompt_version: str = ""
    compiler_version: str = ""
    rules_version: str = ""
    #: The author's own concept -> physical object mapping, kept for audit. It
    #: is evidence about a past decision, never an input to a future one.
    mapping_notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artifact":
        known = {f: data.get(f) for f in cls.__dataclass_fields__ if f in data}
        known.setdefault("capability_id", "")
        known.setdefault("sql", "")
        known.setdefault("origin", ORIGIN_COMPILED)
        return cls(**known)  # type: ignore[arg-type]

    @property
    def usable(self) -> bool:
        """Whether this artifact may be executed to serve a request."""
        return bool(self.sql.strip()) and self.verifier_status in (
            VERIFIED_APPROVED,
            VERIFIED_SEED,
        )

    @property
    def not_applicable(self) -> bool:
        return self.verifier_status == VERIFIED_NOT_APPLICABLE

    def summary(self) -> Dict[str, Any]:
        """A non-secret description, for the status endpoint and the CLI."""
        return {
            "capability": self.capability_id,
            "version": self.version,
            "origin": self.origin,
            "verifier_status": self.verifier_status,
            "validator_status": self.validator_status,
            "generated_at": self.generated_at,
            "model_used": self.model_used,
            "prompt_version": self.prompt_version,
            "compiler_version": self.compiler_version,
            "rules_version": self.rules_version,
            "dependency_fingerprint": self.dependency_fingerprint[:12],
            "tables": (self.dependencies or {}).get("tables", []),
            "not_applicable_reason": self.not_applicable_reason,
        }


@dataclass
class ArtifactRecord:
    """Everything stored for one capability."""

    capability_id: str
    current: Optional[Artifact] = None
    candidate: Optional[Artifact] = None
    history: List[Artifact] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "current": self.current.as_dict() if self.current else None,
            "candidate": self.candidate.as_dict() if self.candidate else None,
            "history": [artifact.as_dict() for artifact in self.history],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactRecord":
        return cls(
            capability_id=str(data.get("capability_id") or ""),
            current=Artifact.from_dict(data["current"]) if data.get("current") else None,
            candidate=(
                Artifact.from_dict(data["candidate"]) if data.get("candidate") else None
            ),
            history=[Artifact.from_dict(item) for item in (data.get("history") or [])],
        )


def _path(capability_id: str):
    return identity.artifact_dir() / f"{capability_id}.json"


def load(capability_id: str) -> ArtifactRecord:
    """Everything stored for one capability; an empty record when nothing is."""
    path = _path(capability_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ArtifactRecord(capability_id=capability_id)
    try:
        return ArtifactRecord.from_dict(json.loads(raw))
    except (ValueError, TypeError, KeyError):
        logger.warning(
            "dynamic: artifact file for %s is unreadable; treating it as absent",
            capability_id,
        )
        return ArtifactRecord(capability_id=capability_id)


def save(record: ArtifactRecord) -> None:
    path = _path(record.capability_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(record.as_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
        tmp.replace(path)
    except OSError:
        logger.warning("dynamic: could not write artifact file %s", path)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def store_candidate(capability_id: str, artifact: Artifact) -> ArtifactRecord:
    """Record a produced-but-not-yet-promoted artifact.

    Written even when it goes on to fail: a candidate that was rejected is the
    most useful thing an operator can read when asking why a capability stopped
    recompiling, and it costs one small file.
    """
    record = load(capability_id)
    artifact.generated_at = artifact.generated_at or now()
    record.candidate = artifact
    save(record)
    return record


def promote(capability_id: str, artifact: Artifact) -> ArtifactRecord:
    """Make a verified candidate the one that serves requests.

    The outgoing artifact moves to history rather than being deleted, so the
    previous answer to "what SQL was this capability running" is always
    recoverable.
    """
    record = load(capability_id)
    previous = record.current
    artifact.version = (previous.version + 1) if previous else 1
    artifact.generated_at = artifact.generated_at or now()
    if previous is not None:
        record.history.insert(0, previous)
        del record.history[_HISTORY_LIMIT:]
    record.current = artifact
    record.candidate = None
    save(record)
    logger.info(
        "dynamic: %s promoted to v%d (%s, %s)",
        capability_id,
        artifact.version,
        artifact.origin,
        artifact.verifier_status,
    )
    return record


def mark_not_applicable(capability_id: str, reason: str, artifact: Artifact) -> ArtifactRecord:
    """Record that this database cannot represent the capability at all.

    A distinct outcome from both approval and rejection, and the only honest
    one when the concepts simply are not recorded here. Approving would store a
    query that answers nothing while reading as a clean result; rejecting would
    spend every remaining attempt asking for a fix no query can make.
    """
    artifact.verifier_status = VERIFIED_NOT_APPLICABLE
    artifact.not_applicable_reason = reason
    artifact.generated_at = artifact.generated_at or now()
    record = load(capability_id)
    if record.current is not None:
        record.history.insert(0, record.current)
        del record.history[_HISTORY_LIMIT:]
    artifact.version = (record.current.version + 1) if record.current else 1
    record.current = artifact
    record.candidate = None
    save(record)
    logger.warning("dynamic: %s marked NOT APPLICABLE - %s", capability_id, reason[:200])
    return record


def identity_matches(
    artifact: Artifact, *, compiler_version: str, prompt_version: str, rules_version: str
) -> bool:
    """Whether this artifact was compiled under the versions in force now.

    A prompt or rule change must retire an artifact even though the database
    did not move: otherwise an improvement takes effect only the next time
    somebody happens to alter a column.
    """
    return (
        artifact.compiler_version == compiler_version
        and artifact.prompt_version == prompt_version
        and artifact.rules_version == rules_version
    )


def staleness(
    artifact: Optional[Artifact],
    capability: Capability,
    snapshot: SchemaSnapshot,
    *,
    compiler_version: str,
    prompt_version: str,
    rules_version: str,
) -> Tuple[bool, str]:
    """(stale, why). The deliberate decision about whether this may still serve.

    Every branch is decidable without a model and without a database round
    trip beyond the snapshot already in hand.
    """
    if artifact is None:
        return True, "no artifact has been compiled for this capability yet"
    if not artifact.sql.strip():
        return True, "the stored artifact carries no SQL"
    if artifact.not_applicable:
        return False, "this capability is recorded as not applicable to this database"
    if not artifact.usable:
        return True, f"the stored artifact was never approved ({artifact.verifier_status})"
    if not identity_matches(
        artifact,
        compiler_version=compiler_version,
        prompt_version=prompt_version,
        rules_version=rules_version,
    ):
        return True, (
            "the compiler, the prompt or the rule text this capability depends on has "
            "changed since it was compiled"
        )

    stored = Dependencies.from_dict(artifact.dependencies or {})
    if not stored.tables:
        # Nothing was recorded, so nothing can be checked. Treated as stale
        # rather than as "nothing changed": an unknown footprint is not a
        # clean bill of health.
        return True, "the artifact records no dependencies, so it cannot be checked"

    live = snapshot.dependency_fingerprint(stored.tables, stored.columns)
    if live != artifact.dependency_fingerprint:
        return True, "the structure of the objects this capability reads has changed"
    return False, "the objects this capability reads are unchanged"


def all_records() -> Dict[str, ArtifactRecord]:
    """Every stored capability, for the status endpoint and the CLI."""
    out: Dict[str, ArtifactRecord] = {}
    directory = identity.artifact_dir()
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return out
    for path in paths:
        record = load(path.stem)
        if record.current or record.candidate:
            out[path.stem] = record
    return out


def clear(capability_id: Optional[str] = None) -> None:
    """Remove stored artifacts. An operator action, never automatic."""
    directory = identity.artifact_dir()
    targets = (
        [directory / f"{capability_id}.json"] if capability_id else list(directory.glob("*.json"))
    )
    for path in targets:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            logger.warning("dynamic: could not remove %s", path)
