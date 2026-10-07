"""The deterministic verifier: every figure in a draft must come from the evidence.

This is the check the single-call summaries never had. It runs on every draft,
costs nothing, and cannot be talked round:

* **numbers and dates** -- each one the draft states must appear somewhere in
  the tool results (or in the question it answers). A figure the writer
  computed, rounded or invented fails.
* **forbidden judgements** -- "delayed", "at risk", "stalled", ... are refused
  (daily_report_rules.md section 9 #11 and its agentic amendment) unless the
  evidence itself carries the word as a value, as the date-validation status
  ``LATE`` does.
* **field-name leaks** -- a snake_case key from the evidence written into the
  prose ("open_task_count") is refused; the rules say plain words.

A failed draft goes back to the writer with the issues listed, at most
AGENT_MAX_REVISIONS times.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, List, Set

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w])")
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
_SNAKE = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")

#: Judgements no rule in this system defines (section 9 #11).
FORBIDDEN_PHRASES = (
    "behind schedule",
    "delayed",
    "delays",
    "at risk",
    "stalled",
    "idle",
    "on track",
    "failing to report",
    "late",
)


@dataclass
class Verification:
    passed: bool
    issues: List[str] = field(default_factory=list)
    checked_numbers: int = 0

    def as_dict(self):
        return {"passed": self.passed, "issues": list(self.issues), "checked_numbers": self.checked_numbers}


def _normalise(token: str) -> str:
    token = token.replace(",", "")
    try:
        value = Decimal(token)
    except InvalidOperation:
        return token
    normalised = value.normalize()
    text = format(normalised, "f")
    return "0" if text in {"-0", ""} else text


def _walk(value: Any, keys: Set[str], texts: List[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            _walk(item, keys, texts)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _walk(item, keys, texts)
    elif isinstance(value, (datetime, date)):
        texts.append(value.isoformat()[:10])
    elif value is not None:
        texts.append(str(value))


def evidence_vocabulary(evidence: Any, extra: Iterable[str] = ()):
    """(numbers, dates, keys, status codes) found in the evidence.

    Status codes are the evidence's upper-case enum values (``LATE``,
    ``NO_ACTUAL``). A forbidden word is allowed only when the evidence carries
    it as such a code -- never merely because some explanatory note in the
    evidence happens to contain it.
    """
    keys: Set[str] = set()
    texts: List[str] = []
    _walk(evidence, keys, texts)
    texts.extend(extra)
    numbers: Set[str] = set()
    dates: Set[str] = set()
    for text in texts:
        dates.update(_DATE.findall(text))
        for token in _NUMBER.findall(_DATE.sub(" ", text)):
            numbers.add(_normalise(token))
            numbers.add(_normalise(token.lstrip("-")))
        # Digit runs inside identifiers (task codes like FLME1180-30356) --
        # never the parts of a date, or "7" would pass because of 2026-07-20.
        for run in re.findall(r"\d+", _DATE.sub(" ", text)):
            numbers.add(_normalise(run))
    codes = {text for text in texts if re.fullmatch(r"[A-Z][A-Z_]*", text)}
    return numbers, dates, keys, codes


def verify(draft: str, evidence: Any, *, question: str = "") -> Verification:
    numbers, dates, keys, codes = evidence_vocabulary(evidence, [question])
    issues: List[str] = []

    body = _LIST_MARKER.sub(" ", draft)
    stated_dates = _DATE.findall(body)
    for stated in sorted(set(stated_dates)):
        if stated not in dates:
            issues.append(f"the date {stated} is not in the evidence")

    stated_numbers = _NUMBER.findall(_DATE.sub(" ", body))
    for token in sorted(set(stated_numbers)):
        normalised = _normalise(token)
        if normalised not in numbers and normalised.lstrip("-") not in numbers:
            issues.append(f"the figure {token} is not in the evidence")

    lowered = f" {draft.lower()} "
    for phrase in FORBIDDEN_PHRASES:
        pattern = rf"(?<![a-z]){re.escape(phrase)}(?![a-z])"
        if re.search(pattern, lowered) and phrase.upper().replace(" ", "_") not in codes:
            issues.append(f'"{phrase}" is a judgement no rule defines; state the facts instead')

    leaked = sorted({token for token in _SNAKE.findall(draft) if token in keys})
    for token in leaked:
        issues.append(f"the field name {token} is written into the text; use plain words")

    return Verification(
        passed=not issues,
        issues=issues,
        checked_numbers=len(set(stated_numbers)) + len(set(stated_dates)),
    )
