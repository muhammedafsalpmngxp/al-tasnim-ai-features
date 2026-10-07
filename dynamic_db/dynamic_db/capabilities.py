"""The generic capability registry engine.

A CAPABILITY IS A BUSINESS QUESTION A DOWNSTREAM APPLICATION ASKS OF A
DATABASE, NOT A QUERY. This module knows nothing about what capabilities
exist for any particular application -- it only defines the SHAPE a
capability takes and the mechanics of registering, looking up and ordering
them. The concrete catalogue (which capabilities exist, what business
concepts they cover, what output columns they must produce, what baseline SQL
seeds them) is DOWNSTREAM CONTENT, supplied by whichever application embeds
this engine, via :func:`register`.

This is the boundary that keeps DYNAMIC_DB reusable: point it at a different
database, for a different application, and this module needs no code change
at all -- only a different set of ``register(Capability(...))`` calls made by
that application at its own startup.

WHAT EACH FIELD IS ALLOWED TO BE
---------------------------------
``concepts``          the BUSINESS terms the capability is about, in business
                      language. No physical name appears, on purpose: the SQL
                      author resolves each concept against the live schema.

``required_columns``  the OUTPUT ALIASES the calling application's own code
                      reads. An application contract, not a statement about
                      the database: whatever physical source the rules
                      resolve to, the compiled SQL must present it under
                      these names.

``parameters``        what the application binds, in order. Always bound,
                      never interpolated.

``rules``             a zero-argument callable that returns the rule text
                      relevant to this capability, as ONE string. How many
                      source documents that text came from, and how it was
                      sliced, is entirely the caller's concern -- this engine
                      only ever sees the result. See ``dynamic_db.rules`` for
                      a reusable numbered-markdown-section slicer a caller can
                      build this callable on top of, if useful.

``baseline_sql``      the human-authored baseline query TEXT (not a filename).
                      Registered as the capability's first verified artifact
                      so that, on an unchanged schema, compiling this
                      capability costs ZERO model calls. Reading it from disk,
                      resolving any include directives, etc. is the caller's
                      job -- this engine only stores and validates the text
                      it is handed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Parameter kinds. The validator and the executor both branch on these, so they
# are stable strings rather than an inline convention.
PARAM_REPORT_DATE = "report_date"
PARAM_INT = "int"
PARAM_TEXT = "text"
PARAM_DATE = "date"


@dataclass(frozen=True)
class Parameter:
    name: str
    kind: str
    description: str
    #: A value the COMPILE PROBE may bind for this parameter when it is a
    #: presentation bound rather than a business value -- a page size, a
    #: limit. A business identifier never has one: inventing a well or a task
    #: to probe with is exactly the hardcoding this engine exists to remove,
    #: so those are borrowed from another capability's verified result
    #: instead (see ``Capability.probe_params_from``).
    probe_default: Optional[Any] = None


def _no_rules() -> str:
    return ""


@dataclass(frozen=True)
class Capability:
    id: str
    intent: str
    concepts: Tuple[str, ...]
    required_columns: Tuple[str, ...]
    parameters: Tuple[Parameter, ...]
    #: The human-authored baseline query, as TEXT. "" means this capability
    #: has no seed and must be compiled before it can serve anything.
    baseline_sql: str = ""
    #: Zero-argument callable returning the rule text this capability should
    #: be compiled and verified against, as one string.
    rules: Callable[[], str] = field(default=_no_rules, compare=False)
    #: One row exactly, always -- including when nothing was recorded.
    single_row: bool = False
    #: The capability reads a table the schema may mark MANY ROWS PER <key>,
    #: so the SQL must resolve to one row per logical thing. Checked
    #: deterministically by the validator, not left to judgement.
    requires_grain_resolution: bool = True
    #: Capability whose already-verified result supplies this one's probe
    #: parameters during compilation. Never a hardcoded identifier.
    probe_params_from: Optional[str] = None
    probe_param_columns: Tuple[str, ...] = ()

    @property
    def parameter_count(self) -> int:
        return len(self.parameters)

    @property
    def requires_report_date(self) -> bool:
        return any(p.kind in (PARAM_REPORT_DATE, PARAM_DATE) for p in self.parameters)

    def rules_text(self) -> str:
        """The rule text for this capability. Never raises: a caller whose
        selector fails leaves the capability with no rule guidance rather
        than breaking the compile outright -- the verifier will simply have
        less to check against, which shows up as a weaker review, not a
        crash."""
        try:
            return self.rules() or ""
        except Exception:  # noqa: BLE001 - a broken selector must not break a compile
            return ""


#: Registered capabilities, in registration order. A plain dict rather than a
#: fixed tuple of known IDs: this engine does not know in advance which
#: capabilities exist, only what one looks like once registered.
REGISTRY: Dict[str, Capability] = {}


def register(capability: Capability) -> Capability:
    """Add (or replace) a capability in the registry. Returns it unchanged,
    so a caller can write ``FOO = register(Capability(...))`` and get back a
    usable constant."""
    REGISTRY[capability.id] = capability
    return capability


def unregister(capability_id: str) -> None:
    REGISTRY.pop(capability_id, None)


def clear() -> None:
    """Remove every registered capability. For tests and for a caller that
    wants to reload its manifest from scratch."""
    REGISTRY.clear()


def get(capability_id: str) -> Capability:
    try:
        return REGISTRY[capability_id]
    except KeyError as exc:
        known = ", ".join(sorted(REGISTRY)) or "(none registered)"
        raise KeyError(f"Unknown capability {capability_id!r}. Known: {known}") from exc


def all_capabilities() -> List[Capability]:
    """Every registered capability, in the order it was registered."""
    return list(REGISTRY.values())


def all_ids() -> Tuple[str, ...]:
    return tuple(REGISTRY.keys())


def compile_order(ids: Sequence[str]) -> List[str]:
    """The order to compile ``ids`` in, so a capability that borrows
    another's result for its probe parameters is never compiled before it."""
    wanted = [i for i in REGISTRY if i in set(ids)]
    ordered: List[str] = []
    for capability_id in wanted:
        source = REGISTRY[capability_id].probe_params_from
        if source and source in wanted and source not in ordered:
            ordered.append(source)
        if capability_id not in ordered:
            ordered.append(capability_id)
    return ordered
