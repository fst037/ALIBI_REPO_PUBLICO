"""Results held by reference — what the agent harness's models see of the data.

Every query the harness runs lands here under a short handle (`r1`, `r2`, …) and
stays server-side. A model is never handed a result whole: it gets a **digest**
(row count, per-column type / distinct count / empties / range / top values, and
a few sample rows) plus the handle, and pulls more with `peek` or `describe` when
it needs to.

Why a budget and not a ban: a model that sees nothing of the rows plans blind,
and one that is shown 5,000 rows is buried — it loses the question under the
data and the run's cost scales with the table. Every observation here is cut to
`harness_observation_tokens` and says so when it is ("20 of 4,312 rows").

Everything here is deterministic and model-free. The numbers it *computes* (a row
count, a distinct count, how many values are empty, how often a top value occurs)
are facts about an executed result, so `shown_figures()` hands them to the
grounding validator: the synthesis may cite "across 12 regions" when it was told
there are 12.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.orchestrator import profile

#: Rough characters per token, for budgeting. Exact counts would need the model's
#: own tokenizer; an observation only has to stay near its budget, not on it.
_CHARS_PER_TOKEN = 4
_SAMPLE_ROWS = 5
_TOP_VALUES = 5
_PEEK_MAX = 50


class ResultLookupError(ValueError):
    """A model asked for a handle or a column that does not exist.

    Returned to the model as its observation — it is a correctable mistake, not a
    failure of the run.
    """


@dataclass
class StoredResult:
    handle: str
    #: The sub-question the planner asked Module A, in its own words.
    question: str
    purpose: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _cell(value: Any) -> Any:
    """A value as JSON can carry it (dates arrive as ISO strings already)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _fit(text: str, budget_tokens: int, note: str) -> str:
    limit = budget_tokens * _CHARS_PER_TOKEN
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(note) - 1)].rstrip() + "\n" + note


@dataclass
class ResultStore:
    """One run's executed results. Lives exactly as long as the run."""

    budget_tokens: int = 1_500
    _results: dict[str, StoredResult] = field(default_factory=dict)
    _figures: set[float] = field(default_factory=set)

    # ---- writing ---------------------------------------------------------------

    def add(
        self,
        *,
        question: str,
        purpose: str,
        sql: str,
        columns: list[str],
        rows: list[dict[str, Any]],
        truncated: bool,
    ) -> StoredResult:
        handle = f"r{len(self._results) + 1}"
        result = StoredResult(handle, question, purpose, sql, list(columns), list(rows), truncated)
        self._results[handle] = result
        self._figures.add(float(result.row_count))
        return result

    # ---- reading ---------------------------------------------------------------

    def get(self, handle: str) -> StoredResult:
        result = self._results.get(str(handle).strip())
        if result is None:
            known = ", ".join(self._results) or "none yet"
            raise ResultLookupError(f"There is no result called {handle!r} (known: {known}).")
        return result

    def all(self) -> list[StoredResult]:
        return list(self._results.values())

    def shown_figures(self) -> set[float]:
        """Every figure this store computed and showed a model. See the module docstring."""
        return set(self._figures)

    def _column(self, result: StoredResult, column: str) -> str:
        if column not in result.columns:
            raise ResultLookupError(
                f"{result.handle} has no column {column!r} (columns: {', '.join(result.columns)})."
            )
        return column

    # ---- observations ----------------------------------------------------------

    def _column_line(self, result: StoredResult, column: str, top: int) -> str:
        values = [row.get(column) for row in result.rows]
        present = [v for v in values if v is not None]
        empty = len(values) - len(present)
        (described,) = profile.column_profile(result.rows, [column])
        self._figures.update({float(described.cardinality), float(empty)})
        parts = [f"{described.dtype}", f"{described.cardinality} distinct"]
        if empty:
            parts.append(f"{empty} empty")
        if present and described.dtype in ("quantitative", "temporal"):
            try:
                parts.append(f"from {_cell(min(present))} to {_cell(max(present))}")
            except TypeError:  # mixed types: no order to report
                pass
        if present and described.dtype in ("nominal", "temporal") and described.cardinality > 1:
            counts = Counter(str(v) for v in present).most_common(top)
            self._figures.update(float(n) for _, n in counts)
            parts.append("top: " + ", ".join(f"{value} ({n})" for value, n in counts))
        return f"- {column}: " + "; ".join(parts)

    def digest(self, handle: str) -> str:
        """What a model sees of a result by default."""
        result = self.get(handle)
        size = f"{result.row_count} rows" + (
            " (cut at the query limit; there are more)" if result.truncated else ""
        )
        lines = [f"{result.handle} [{result.purpose}] — {result.question}", size]
        if result.row_count:
            lines.append("columns:")
            lines += [self._column_line(result, c, _TOP_VALUES) for c in result.columns]
            sample = [{c: _cell(row.get(c)) for c in result.columns} for row in result.rows[:_SAMPLE_ROWS]]
            lines.append(f"first {len(sample)} of {result.row_count} rows:")
            lines.append(json.dumps(sample, ensure_ascii=False, default=str))
            if result.row_count > len(sample):
                lines.append(f"(use peek on {result.handle} to see more)")
        return _fit(
            "\n".join(lines),
            self.budget_tokens,
            f"(digest cut to fit; use describe or peek on {result.handle})",
        )

    def peek(
        self,
        handle: str,
        *,
        columns: list[str] | None = None,
        where: dict[str, Any] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        offset: int = 0,
        limit: int = 20,
    ) -> str:
        """Rows of a stored result: some columns, an equality filter, an order."""
        result = self.get(handle)
        chosen = [self._column(result, c) for c in (columns or result.columns)]
        rows = result.rows
        for column, wanted in (where or {}).items():
            self._column(result, column)
            rows = [r for r in rows if r.get(column) == wanted or str(r.get(column)) == str(wanted)]
        if order_by:
            self._column(result, order_by)
            present = [r for r in rows if r.get(order_by) is not None]
            missing = [r for r in rows if r.get(order_by) is None]
            try:
                present = sorted(present, key=lambda r: r[order_by], reverse=descending)
            except TypeError:
                present = sorted(present, key=lambda r: str(r[order_by]), reverse=descending)
            rows = present + missing
        limit = max(1, min(int(limit), _PEEK_MAX))
        offset = max(0, int(offset))
        window = rows[offset : offset + limit]
        body = json.dumps(
            [{c: _cell(r.get(c)) for c in chosen} for r in window], ensure_ascii=False, default=str
        )
        header = f"{result.handle}: rows {offset + 1}-{offset + len(window)} of {len(rows)}"
        if where:
            header += " matching the filter"
        self._figures.add(float(len(rows)))
        return _fit(
            f"{header}\n{body}",
            self.budget_tokens,
            f"(cut to fit: ask for fewer columns or a smaller limit on {result.handle})",
        )

    def describe(self, handle: str, column: str) -> str:
        """One column in full: type, distinct values, empties, range, the top values."""
        result = self.get(handle)
        self._column(result, column)
        return _fit(
            f"{result.handle}.{column} over {result.row_count} rows\n"
            + self._column_line(result, column, top=15),
            self.budget_tokens,
            "(cut to fit)",
        )


__all__ = ["ResultLookupError", "ResultStore", "StoredResult"]
