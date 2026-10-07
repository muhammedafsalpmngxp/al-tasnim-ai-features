"""Static audit: has a physical database name crept back into generic code?

THE WHOLE ARCHITECTURE RESTS ON ONE PROPERTY -- that no generic prompt, node or
graph decision names a table or a column. That property is easy to state, easy
to believe, and very easy to lose: one helpful-looking line in a prompt
("resolve this against the daily task table") reinstates exactly the dependency
this package was built to remove, and nothing at runtime would ever complain.
So it is checked mechanically, and the check is a test.

WHERE PHYSICAL KNOWLEDGE IS ALLOWED, AND WHY EACH ONE IS LEGITIMATE

  runtime introspection   it reads the names; it does not contain them.
  the rule documents      authoritative domain documentation, written and read
                          by people, and the one place a business definition is
                          allowed to say how something is currently stored.
  the baseline .sql files and compiled artifacts
                          these ARE the physical implementation. Their whole
                          job is to name objects.
  tests and fixtures      a fixture that cannot name a column cannot test one.

TWO KINDS OF MENTION, AND ONLY ONE OF THEM CAN BREAK.
A name inside a comment or a docstring is an explanation; it can go out of date
and mislead a reader, which is worth reporting, but it cannot change what the
code does. A name in executable code -- a string the program acts on, an
attribute it reads -- is a dependency. They are separated here, and the test
that guards this property fails only on the second.

WHOLE TOKENS, NEVER SUBSTRINGS. This matters more than it looks. An earlier
version matched substrings and reported ``task_daily_id`` -- an application
field name -- as a reference to a table called ``task_daily``, along with about
fifty others. Fifty false findings is not a strict audit; it is an audit
nobody reads.

WHAT IS SEARCHED FOR is derived from the ALLOWLIST at runtime, never typed into
this file -- so pointing the application at a different database audits that
database's names with no edit here.
"""

from __future__ import annotations

import io
import re
import token as token_module
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from dynamic_db.config import PACKAGE_ROOT, get_settings

#: Paths, relative to whatever root is scanned, where physical knowledge
#: legitimately belongs BY DEFAULT -- this project's own tests, its own
#: cache, and this file (which describes the rule, so it may state it).
#: A caller auditing a DIFFERENT tree (a downstream application's own source,
#: which has its own baseline SQL and rule documents) passes its own
#: ``allowed_paths`` to :func:`run` rather than editing this default.
DEFAULT_ALLOWED_PATHS = (
    "tests/",
    ".cache/",
    "dynamic_db/audit.py",
)

#: The zone whose cleanliness this architecture depends on absolutely: the
#: whole compile engine. A single finding here, of any kind, is a real
#: regression. A caller auditing its own tree passes its own ``strict_zone``.
DEFAULT_STRICT_ZONE = "dynamic_db/"

#: Statement shapes that must never appear in this application at all. The
#: engine is read-only by contract, and a contract is worth checking rather
#: than assuming. Each requires enough context to be a statement rather than a
#: word: bare "GRANT" also appears in a T-SQL reserved-word list, which is a
#: list of words precisely so that queries using them can be quoted correctly.
WRITE_STATEMENTS = re.compile(
    r"\bINSERT\s+INTO\s+\w"
    r"|\bUPDATE\s+[\w.\[\]]+\s+SET\b"
    r"|\bDELETE\s+FROM\s+\w"
    r"|\bMERGE\s+INTO\s+\w"
    r"|\bDROP\s+(TABLE|VIEW|DATABASE|INDEX)\s+\w"
    r"|\bALTER\s+(TABLE|DATABASE)\s+\w"
    r"|\bCREATE\s+(TABLE|VIEW|INDEX|DATABASE)\s+\w"
    r"|\bTRUNCATE\s+TABLE\s+\w"
    r"|\bGRANT\s+\w+\s+ON\b"
    r"|\bREVOKE\s+\w+\s+ON\b",
    re.IGNORECASE,
)

#: A date written into code where a bound report date belongs.
HARDCODED_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?")

KIND_CODE = "code"
KIND_DOC = "documentation"


@dataclass
class Finding:
    path: str
    line: int
    kind: str  # code | documentation
    rule: str  # physical-name | write-statement | hardcoded-date
    text: str
    #: Whether the name was written as a real object reference -- qualified by
    #: a schema, or qualifying a column. A BARE table name is ambiguous with
    #: this application's own vocabulary by construction: one of the reference
    #: tables here is named the same as an API field, and reporting every
    #: occurrence of that field as a database dependency is how an audit
    #: becomes noise nobody reads.
    qualified: bool = False
    #: Whether this finding sits inside the strict zone. Set by :func:`run`
    #: from whatever ``strict_zone`` it was called with -- never read from a
    #: module-level global, so auditing two different trees in one process
    #: (this project's own, and a downstream caller's) never cross-contaminates.
    in_strict_zone: bool = False

    @property
    def zone(self) -> str:
        return "strict" if self.in_strict_zone else "application"

    def __str__(self) -> str:
        return f"{self.path}:{self.line} [{self.rule}/{self.kind}] {self.text.strip()[:140]}"


def _is_allowed(relative: str, allowed_paths: Sequence[str]) -> bool:
    normalised = relative.replace("\\", "/")
    return any(normalised.startswith(prefix) for prefix in allowed_paths)


def physical_names(extra: Optional[Iterable[str]] = None) -> Set[str]:
    """The names this audit looks for, taken from the live allowlist.

    Both halves of each allowlisted object -- the qualified name and the bare
    table name -- because either spelling in a prompt is the same dependency.
    Very short names are dropped: a four-letter table name matched as a word
    would still collide with ordinary English often enough to bury the real
    findings.
    """
    names: Set[str] = set()
    for qualified in get_settings().included_tables:
        names.add(qualified.lower())
        table = qualified.split(".", 1)[-1]
        if len(table) > 4:
            names.add(table.lower())
    for name in extra or ():
        if len(name) > 4:
            names.add(name.lower())
    return names


def _pattern(names: Set[str]) -> Optional[re.Pattern]:
    """One whole-token pattern for every name, longest first.

    Longest first so ``well.task_daily`` is reported as itself rather than as a
    bare ``task_daily`` sitting next to a schema name.
    """
    if not names:
        return None
    ordered = sorted(names, key=len, reverse=True)
    return re.compile(
        r"(?<![\w])(" + "|".join(re.escape(name) for name in ordered) + r")(?![\w])",
        re.IGNORECASE,
    )


def _classified_lines(source: str) -> Dict[int, str]:
    """line number -> KIND_DOC when that line is only comment or docstring.

    Uses Python's own tokeniser rather than a heuristic: a line is
    documentation when every token on it is a comment, a docstring, or
    structural. Anything else makes it code, and a physical name on it is a
    dependency rather than an explanation.
    """
    doc_lines: Set[int] = set()
    code_lines: Set[int] = set()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return {}

    previous_significant = token_module.NEWLINE
    for tok in tokens:
        start_line, end_line = tok.start[0], tok.end[0]
        if tok.type == token_module.COMMENT:
            for line in range(start_line, end_line + 1):
                doc_lines.add(line)
            continue
        if tok.type in (
            token_module.NL,
            token_module.NEWLINE,
            token_module.INDENT,
            token_module.DEDENT,
            token_module.ENDMARKER,
        ):
            if tok.type == token_module.NEWLINE:
                previous_significant = token_module.NEWLINE
            continue
        if tok.type == token_module.STRING and previous_significant in (
            token_module.NEWLINE,
            token_module.INDENT,
            token_module.DEDENT,
        ):
            # A string that is a statement on its own: a module, class or
            # function docstring.
            for line in range(start_line, end_line + 1):
                doc_lines.add(line)
            previous_significant = tok.type
            continue
        for line in range(start_line, end_line + 1):
            code_lines.add(line)
        previous_significant = tok.type

    return {line: KIND_DOC for line in doc_lines if line not in code_lines}


def scan_file(
    path: Path,
    names: Set[str],
    root: Path,
    *,
    allowed_paths: Sequence[str],
    strict_zone: str,
) -> List[Finding]:
    relative = str(path.relative_to(root))
    if _is_allowed(relative, allowed_paths):
        return []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    in_strict_zone = relative.replace("\\", "/").startswith(strict_zone)
    pattern = _pattern(names)
    kinds = _classified_lines(source) if path.suffix == ".py" else {}
    findings: List[Finding] = []
    for number, line in enumerate(source.splitlines(), start=1):
        kind = kinds.get(number, KIND_CODE)
        if pattern is not None:
            match = pattern.search(line)
            if match is not None:
                findings.append(
                    Finding(
                        relative, number, kind, "physical-name", line,
                        qualified=_is_qualified(line, match),
                        in_strict_zone=in_strict_zone,
                    )
                )
        if WRITE_STATEMENTS.search(line):
            findings.append(
                Finding(relative, number, kind, "write-statement", line, True, in_strict_zone)
            )
        if HARDCODED_DATE.search(line) and ('"' in line or "'" in line):
            findings.append(
                Finding(relative, number, kind, "hardcoded-date", line, True, in_strict_zone)
            )
    return findings


def _is_qualified(line: str, match: re.Match) -> bool:
    """Whether this occurrence is written as a real object reference.

    Three shapes count: the matched name already carries a schema
    (``schema.table``), it is followed by a dotted member (``table.column``),
    or it is preceded by one (``schema.table`` matched on the bare half).
    A bare name standing alone is left as ambiguous.
    """
    if "." in match.group(0):
        return True
    after = line[match.end() : match.end() + 2]
    if after.startswith(".") and len(after) > 1 and (after[1].isalnum() or after[1] == "_"):
        return True
    before = line[max(0, match.start() - 1) : match.start()]
    return before == "."


def run(
    root: Optional[Path] = None,
    extra_names: Optional[Sequence[str]] = None,
    *,
    allowed_paths: Sequence[str] = DEFAULT_ALLOWED_PATHS,
    strict_zone: str = DEFAULT_STRICT_ZONE,
) -> List[Finding]:
    """Every finding across ``root``'s Python sources.

    Defaults to auditing THIS project's own tree. A downstream application
    auditing its own source (its manifest module, its API routes) passes its
    own ``root``, ``allowed_paths`` (where ITS baseline SQL and rule
    documents legitimately live) and ``strict_zone`` (wherever ITS own
    generic prompt/orchestration code lives, if any).
    """
    root = root or PACKAGE_ROOT
    names = physical_names(extra_names)
    findings: List[Finding] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        findings.extend(
            scan_file(path, names, root, allowed_paths=allowed_paths, strict_zone=strict_zone)
        )
    return findings


def blocking(findings: Sequence[Finding]) -> List[Finding]:
    """The findings that are genuine regressions rather than stale wording.

    Inside the strict zone: ANY mention, bare or qualified, comment or code.
    That zone has no legitimate reason to contain a physical name in any
    form, so no exception is needed and none is made.

    Everywhere else: only a QUALIFIED reference in executable code. A bare
    table name in application code is ambiguous with that application's own
    vocabulary, and a docstring that names a column is stale wording, not a
    dependency.
    """
    return [
        f
        for f in findings
        if f.zone == "strict" or (f.kind == KIND_CODE and f.qualified)
    ]


def summarise(findings: Sequence[Finding]) -> Dict[Tuple[str, str], int]:
    counts: Dict[Tuple[str, str], int] = {}
    for finding in findings:
        key = (finding.rule, finding.kind)
        counts[key] = counts.get(key, 0) + 1
    return counts
