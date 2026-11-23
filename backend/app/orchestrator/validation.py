"""The Reflection layer: check a narrative before the user ever sees it.

Rules 1, 2 and 7 of the specification — strict grounding, zero technical jargon,
one to three sentences — are instructions in a prompt, and a prompt is a request,
not a guarantee. This module turns each of them into a check that runs on the
generated text, so a violation is caught and retried rather than shipped.

The checks are ordered by how much damage the failure does:

1. **Grounding.** A fabricated number is the only failure a user cannot detect.
   Wrong-sounding prose is obvious; "sales grew 34%" when nothing in the result
   says 34 is indistinguishable from the truth, and it is the failure mode that
   makes a BI tool untrustworthy rather than merely annoying.
2. **Jargon.** Leaks the database the product spent its whole design hiding.
3. **Length.** Cosmetic, but the narrative sits above a chart and a results
   table; a paragraph there buries both.

Every failure produces a *critique string* rather than a bare False, because the
retry (section 6) re-prompts with the critique attached. Telling the model
"the figure 34 does not appear in the data" is what makes the second attempt
different from the first; telling it "invalid" only rerolls the dice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Numbers as the data writes them (en-US: optional sign, comma thousands
# separators, optional decimals, optional trailing percent). The prose is in
# Spanish but its figures keep this format — the prompts require it, because the
# table and chart beside the answer show it — so a Spanish-style "36,4" parses
# as 364 and is rejected as ungrounded, and the retry asks for "36.4". Currency
# symbols are left out of the match and handled by the caller stripping them,
# so "$1,234.50" yields 1234.50.
_NUMBER = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?%?")

_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

# Words that admit there is a database. Deliberately narrow: banning ordinary
# English that happens to be SQL syntax ("from", "where", "order", "group",
# "count") would reject correct sentences constantly, and the only terms that
# actually break the illusion are the ones that name the machinery. Multi-word
# entries are matched as phrases so "group by" is caught while "group" alone is
# not.
_JARGON = [
    "sql", "query", "queries", "queried", "querying",
    "database", "table", "tables", "column", "columns",
    "row", "rows", "schema", "dataset", "datasets",
    "join", "joined", "inner join", "left join",
    "group by", "order by", "select statement", "subquery",
    "primary key", "foreign key", "null value", "nulls",
    "varchar", "integer type", "data type", "result set",
    "record set", "aggregation function", "truncated",
    # The answer is written in Spanish, so its jargon is too. Same narrowness:
    # "consulta" alone is left out because it is also the product's word for a
    # question and an ordinary business noun (a customer's inquiry), and
    # "registro" because it names business logs ("registro de ventas").
    "consulta sql", "consultas sql", "subconsulta", "subconsultas",
    "base de datos", "bases de datos", "tabla", "tablas",
    "columna", "columnas", "fila", "filas", "esquema",
    "conjunto de datos", "conjuntos de datos",
    "clave primaria", "clave foránea", "valor nulo", "valores nulos", "nulos",
    "tipo de dato", "tipo de datos", "truncado", "truncada", "truncados", "truncadas",
]
_JARGON_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in sorted(_JARGON, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

#: How far a cited figure may sit from the value in the data purely through
#: honest rounding. Compared after rounding the source value to the *narrative's
#: own* decimal precision, so "36.4" matches a stored 36.42 while "36.9" does
#: not — rule 1 forbids misleading rounding, not rounding.
_ROUNDING_EPSILON = 1e-9

_MAX_SENTENCES = 3


@dataclass
class Critique:
    """The verdict, plus what to tell the model if it has to try again."""

    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_prompt(self) -> str:
        joined = "\n".join(f"- {p}" for p in self.problems)
        return (
            "Your previous answer was rejected for these reasons:\n"
            f"{joined}\n"
            "Write it again, fixing every point. Use only figures that appear in "
            "the data you were given."
        )


def _parse_number(token: str) -> tuple[float, int] | None:
    """`("1,234.50%", …)` → `(1234.50, 2)`: the value and its decimal places."""
    cleaned = token.replace(",", "").replace("$", "").rstrip("%")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    _, _, fraction = cleaned.partition(".")
    return value, len(fraction)


def _numbers_in(text: str) -> list[tuple[str, float, int, bool]]:
    """Every number in `text` as (raw, value, decimals, is_percent)."""
    found = []
    for match in _NUMBER.finditer(text):
        raw = match.group(0)
        parsed = _parse_number(raw)
        if parsed is not None:
            found.append((raw, parsed[0], parsed[1], raw.endswith("%")))
    return found


def grounded_values(rows: list[dict[str, Any]], question: str = "") -> set[float]:
    """Every number the narrative is allowed to cite.

    Three sources, all of which are things the user either supplied or was shown:

    * numeric cells, as they are;
    * numbers embedded in text cells — a product code like `A-250` or a period
      label like `Q3 2024` is data, and quoting it back is grounded;
    * numbers in the user's own question ("sales in 2025"), which the narrative
      may legitimately repeat to say *which* slice it is describing even when the
      result itself never carries the year as a value.
    """
    values: set[float] = set()
    for row in rows:
        for cell in row.values():
            if isinstance(cell, bool) or cell is None:
                continue
            if isinstance(cell, (int, float)):
                values.add(float(cell))
            elif isinstance(cell, str):
                for _, value, _, _ in _numbers_in(cell):
                    values.add(value)
    for _, value, _, _ in _numbers_in(question):
        values.add(value)
    return values


def _matches(cited: float, decimals: int, allowed: set[float]) -> bool:
    for value in allowed:
        if abs(value - cited) < _ROUNDING_EPSILON:
            return True
        if abs(round(value, decimals) - cited) < _ROUNDING_EPSILON:
            return True
    return False


def check_grounding(
    narrative: str,
    rows: list[dict[str, Any]],
    *,
    question: str = "",
    row_count: int = 0,
    extra_values: set[float] | None = None,
    counting_limit: int | None = None,
) -> list[str]:
    """Rule 1. Every figure in the sentence must exist in the data.

    Two allowances beyond a literal value match, both for claims that are about
    the result rather than *in* it:

    * **Counting the result.** "the top three regions", "across 12 categories" —
      any integer up to the number of rows returned, plus the row count itself.
      The narrative is describing the shape of what came back, which it can see.
    * **Percent scaling.** A stored `0.364` written as `36.4%` is the same fact
      in the unit a reader expects, so both directions of the ×100 conversion
      count as grounded. Without this the model is forced to write "0.364 of
      sales", which satisfies the letter of rule 1 and defeats rule 4.

    `extra_values` are figures computed from executed results rather than read
    off a cell (a result's row count, a column's distinct count). `counting_limit`
    replaces the row-count allowance when `rows` is the union of several results:
    counting "up to the rows returned" means up to one result's rows, not their sum.
    """
    allowed = grounded_values(rows, question) | (extra_values or set())
    limit = counting_limit if counting_limit is not None else max(row_count, len(rows))
    counting = {float(n) for n in range(0, limit + 1)}
    problems = []
    for raw, value, decimals, is_percent in _numbers_in(narrative):
        if _matches(value, decimals, allowed) or value in counting:
            continue
        scaled = {v * 100 for v in allowed} | {v / 100 for v in allowed}
        if is_percent and _matches(value, decimals, scaled):
            continue
        problems.append(
            f"The figure “{raw}” does not appear anywhere in the data. "
            "Remove it or replace it with a value that does."
        )
    return problems


def _is_machine_name(term: str) -> bool:
    """Whether an identifier is recognisably one, rather than an ordinary word.

    This is the difference between catching a leak and censoring correct English.
    A column called `region` or `share` is *also* the natural business word for
    what it holds, and a narrative is supposed to say "region" — flagging it
    would reject the very sentence the system is trying to produce, including its
    own fallback templates. A column called `fch_vcto`, `total_revenue` or
    `col_3` cannot be mistaken for prose.

    The separator is the signal: an underscore, a hyphen, a digit, or an
    embedded capital marks a name that was written for a machine. A single
    lowercase word was not, whatever it happens to be.
    """
    if len(term) < 4:
        # "id", "qty" — too short to distinguish from ordinary words at all.
        return False
    return bool(re.search(r"[_\-0-9]", term) or re.search(r"[a-z][A-Z]", term))


def check_jargon(narrative: str, extra_terms: list[str] | None = None) -> list[str]:
    """Rule 2. The user must never learn there is a database behind this.

    `extra_terms` carries the identifiers this specific query touched — table
    names, physical column names — which are jargon by definition even though no
    fixed list could contain them.
    """
    problems = []
    for match in dict.fromkeys(m.lower() for m in _JARGON_PATTERN.findall(narrative)):
        problems.append(
            f"The word “{match}” is technical vocabulary. Describe the finding in "
            "business terms with no reference to how the data is stored or retrieved."
        )
    for term in extra_terms or []:
        if not _is_machine_name(term):
            continue
        if re.search(rf"\b{re.escape(term)}\b", narrative, re.IGNORECASE):
            problems.append(
                f"“{term}” is an internal name. Use the business meaning of that "
                "value instead."
            )
    return problems


def check_length(narrative: str) -> list[str]:
    """Rule 7. One to three sentences — this precedes a chart, it isn't a report."""
    text = narrative.strip()
    if not text:
        return ["The answer is empty."]
    sentences = len(_SENTENCE_END.findall(text)) or 1
    if sentences > _MAX_SENTENCES:
        return [
            f"The answer runs to {sentences} sentences. Say it in "
            f"{_MAX_SENTENCES} or fewer."
        ]
    return []


def review(
    narrative: str,
    rows: list[dict[str, Any]],
    *,
    question: str = "",
    row_count: int = 0,
    internal_names: list[str] | None = None,
) -> Critique:
    """Run every check. An empty `Critique` means the narrative may be sent."""
    return Critique(
        problems=(
            check_grounding(narrative, rows, question=question, row_count=row_count)
            + check_jargon(narrative, internal_names)
            + check_length(narrative)
        )
    )


def review_across(
    text: str,
    results: list[list[dict[str, Any]]],
    *,
    question: str = "",
    extra_values: set[float] | None = None,
    internal_names: list[str] | None = None,
    max_sentences: int = _MAX_SENTENCES,
) -> list[str]:
    """`review` for an answer built from several results (the agent harness).

    A figure is grounded if it appears in **any** executed result — that is the
    harness's rule ("every figure in the answer comes from an executed result").
    The counting allowance is the largest single result, not the union: "across
    12 regions" is a count of one result, and summing every step's rows would
    let almost any small integer through.

    Returns the problems rather than a `Critique`, because the caller reviews
    several texts (the summary and each finding's sentence) into one critique.
    """
    union = [row for rows in results for row in rows]
    problems = check_grounding(
        text,
        union,
        question=question,
        extra_values=extra_values,
        counting_limit=max((len(rows) for rows in results), default=0),
    )
    problems += check_jargon(text, internal_names)
    stripped = text.strip()
    if not stripped:
        problems.append("The text is empty.")
    else:
        sentences = len(_SENTENCE_END.findall(stripped)) or 1
        if sentences > max_sentences:
            problems.append(
                f"“{stripped[:60]}…” runs to {sentences} sentences. Say it in "
                f"{max_sentences} or fewer."
            )
    return problems


__all__ = [
    "Critique",
    "check_grounding",
    "check_jargon",
    "check_length",
    "grounded_values",
    "review",
    "review_across",
]
