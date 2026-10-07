"""A reusable slicer for numbered markdown rule documents.

THIS MODULE DOES NOT KNOW WHICH DOCUMENTS EXIST. It slices whatever
``## N. Title`` sectioned markdown file a caller points it at, by whichever
section numbers that caller declares it needs. Which documents an
application's rules live in, how many there are, and which sections a given
capability needs are all downstream decisions -- see
``Capability.rules`` in :mod:`dynamic_db.capabilities` for how a caller wires
the result of this module into something the compile engine can use.

WHY SLICE AT ALL, RATHER THAN SEND THE WHOLE DOCUMENT EVERY TIME
------------------------------------------------------------------
Sending an entire rulebook to every capability's compile is the single
largest avoidable token cost available to an application built on this
engine, and a section a capability cannot use is a section it can only be
confused by.

A SAVING THAT REMOVES A RULE THE CAPABILITY NEEDED IS NOT A SAVING. So the
selection is by DECLARED section number, not by a keyword or word-frequency
match: a declaration cannot drift the way a keyword match can. And when a
declared section is not found -- the document was renumbered or reorganised
-- the WHOLE document is returned instead and the mismatch is logged loudly.
Degrading to "more rules than needed" is safe; degrading to "fewer" is not.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
import re

logger = logging.getLogger(__name__)

_SECTION = re.compile(r"^##\s+(\d+)\.\s*(.+?)\s*$", re.MULTILINE)
#: A section whose title says it constrains how the rules themselves are
#: read. Always included whatever a caller declared: it is typically a
#: handful of lines, and it is what stands between "resolve this concept" and
#: "invent one". A caller with no such section pays nothing extra for this.
_ALWAYS_TITLES = ("strict instruction", "not yet defined")


@dataclass(frozen=True)
class Section:
    number: int
    title: str
    text: str


@lru_cache(maxsize=32)
def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.warning("rules: document %s could not be read", path)
        return ""


@lru_cache(maxsize=32)
def sections(path: str) -> Tuple[Section, ...]:
    """Every ``## N. Title`` section of one document, in file order."""
    text = _read(path)
    if not text.strip():
        return ()
    matches = list(_SECTION.finditer(text))
    out: List[Section] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out.append(
            Section(
                number=int(match.group(1)),
                title=match.group(2).strip(),
                text=text[start:end].strip(),
            )
        )
    return tuple(out)


def preamble(path: str) -> str:
    """Everything before the first numbered section.

    This is typically where a document states what it is authoritative for --
    short, and never omitted when non-empty.
    """
    text = _read(path)
    match = _SECTION.search(text)
    return (text[: match.start()] if match else text).strip()


def select(path: str, numbers: Sequence[int]) -> str:
    """The declared sections of one document, plus what is always included.

    Returns "" when nothing was declared. Returns the WHOLE document when a
    declared section is missing, because a renumbered document must never
    silently drop a rule.
    """
    available = sections(path)
    if not available or not numbers:
        return ""

    by_number: Dict[int, Section] = {section.number: section for section in available}
    missing = [number for number in numbers if number not in by_number]
    if missing:
        logger.warning(
            "rules: %s has no section(s) %s any more - sending the whole document "
            "rather than dropping a rule",
            path,
            ", ".join(str(n) for n in missing),
        )
        return _read(path).strip()

    wanted = {int(n) for n in numbers}
    for section in available:
        if any(marker in section.title.lower() for marker in _ALWAYS_TITLES):
            wanted.add(section.number)

    chosen = [section for section in available if section.number in wanted]
    parts = [preamble(path)] if preamble(path) else []
    parts.extend(section.text for section in chosen)
    return "\n\n".join(part for part in parts if part).strip()


def combine(*texts: str) -> str:
    """Join several already-selected texts into one string, skipping blanks.

    A small convenience for a caller building a capability's ``rules``
    callable out of more than one document.
    """
    return "\n\n".join(t for t in texts if t and t.strip()).strip()


def version(text: str) -> str:
    """A short, stable identity for a block of rule text.

    Part of a compiled artifact's cache key, so editing text a capability
    depends on retires that capability's artifact, while editing unrelated
    text leaves it alone -- because only the SELECTED text is hashed, not the
    whole source document.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def reload() -> None:
    """Drop every cached document read. Used by tests and by an operator
    who has edited a rule document and wants the next compile to see it."""
    _read.cache_clear()
    sections.cache_clear()


def describe(paths: Sequence[str]) -> Dict[str, object]:
    """What is loaded from the given documents, for a status endpoint or CLI."""
    return {
        path: {
            "present": bool(_read(path).strip()),
            "sections": [f"{s.number}. {s.title}" for s in sections(path)],
            "chars": len(_read(path)),
        }
        for path in paths
    }
