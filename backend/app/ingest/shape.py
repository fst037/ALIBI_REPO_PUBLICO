"""Turn a raw grid of cells into a table with a header and only real rows.

This is where "the user's file is not a clean CSV" gets handled. Real exports
arrive with a title line above the data, a blank row under it, a two-row merged
header, a "TOTAL" line at the bottom, and a stray column of notes off to the
right. None of that is malformed enough to fail on — but all of it produces a
useless table if fed straight to a parser.

The header-detection heuristic, which is the crux: a header row is one whose
cells are *labels* while the rows beneath it are *values*. So instead of
assuming row 0, score each of the first rows on how header-like it is versus
how value-like the rows below it are, and take the best. That single idea
handles preamble titles, blank leading rows, and headerless files alike — and
when nothing scores well, the honest answer is to synthesise names and mark
the confidence `baja` so the confirmation screen asks the user to look.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Callable

from app.ingest.models import Cell, Grid, RawTable, ShapedTable
from app.ingest.typing_rules import parse_bool, parse_date, parse_number
from app.schemas.datasets import InferenceConfidence

logger = logging.getLogger(__name__)

# How many leading rows to consider as possible headers. Beyond ~15 rows of
# preamble it stops being a spreadsheet and starts being a document.
MAX_HEADER_SCAN = 15

# A layout arbiter answers exactly one question about a grid: which row is the
# header? It returns the 0-based index, or None for "no header" / "can't tell".
#
# It is a *parameter* rather than something this module reaches out to, which is
# what keeps the pipeline pure and offline-testable: the model call lives in
# `app.ingest.inference`, and the tests pass a plain function.
LayoutArbiter = Callable[[Grid], int | None]

# The excerpt an arbiter is allowed to see. Bounded because it is the one place
# in Module B where actual cell values (not just a profile) leave the process,
# which has a cost and a privacy price.
MAX_ARBITRATION_ROWS = 15
MAX_ARBITRATION_COLS = 20
MAX_ARBITRATION_CELL = 60

# Labels that mark a summary row rather than a data row.
_TOTAL_TOKENS = re.compile(
    r"^\s*(total(es)?|subtotal(es)?|suma|sumatoria|promedio|media|acumulado|gran\s+total)\b",
    re.IGNORECASE,
)


def _cell_text(cell: Cell) -> str:
    return "" if cell is None else str(cell).strip()


def _is_value_like(cell: Cell) -> bool:
    """True when the cell reads as data rather than as a label."""
    text = _cell_text(cell)
    if not text:
        return False
    if parse_number(text) is not None:
        return True
    if parse_date(text) is not None:
        return True
    return parse_bool(text) is not None and text.lower() not in {"x", "s", "n", "y"}


def _row_fill(row: list[Cell]) -> int:
    return sum(1 for c in row if _cell_text(c))


# ---------------------------------------------------------------------------
# Grid tidying
# ---------------------------------------------------------------------------


def _rectangularise(grid: Grid) -> Grid:
    width = max((len(r) for r in grid), default=0)
    return [list(r) + [None] * (width - len(r)) for r in grid]


def _drop_empty_rows(grid: Grid) -> Grid:
    return [row for row in grid if _row_fill(row) > 0]


def _drop_empty_columns(grid: Grid) -> tuple[Grid, int]:
    """Remove columns that are empty top to bottom.

    Returns the trimmed grid and how many columns went, so the caller can note
    it — a spreadsheet with a spacer column between two blocks is common, and
    silently dropping it would be confusing if the user counted columns.
    """
    if not grid:
        return grid, 0
    width = len(grid[0])
    keep = [i for i in range(width) if any(_cell_text(row[i]) for row in grid)]
    if len(keep) == width:
        return grid, 0
    return [[row[i] for i in keep] for row in grid], width - len(keep)


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------


def _column_contrast(grid: Grid, index: int) -> tuple[float, int]:
    """Per-column type contrast between row `index` and the rows below it.

    This is the signal that actually separates a header from a data row, and
    it has to be computed per column rather than over the row as a whole.
    Consider a headerless table:

        Córdoba | 100
        Rosario | 250

    Row 0 is 50% labels and 50% values — and so is every row beneath it. Judged
    row-wise it looks exactly as header-like as the data does. Judged per
    column, the picture is unambiguous: column 1 holds numbers *in the
    candidate row too*, so there is no contrast anywhere and this is not a
    header.

    Only columns whose body is value-like can testify (a text column can't tell
    a label from a value), and an empty candidate cell counts as no evidence —
    otherwise a title row, which is blank across most columns, would score a
    perfect contrast against a numeric body.

    Returns (contrast, number of columns that could testify).
    """
    width = len(grid[index])
    body = grid[index + 1 : index + 21]
    if not body:
        return 0.0, 0

    testifying = 0
    agreeing = 0
    for col in range(width):
        body_cells = [r[col] for r in body if col < len(r) and _cell_text(r[col])]
        if not body_cells:
            continue
        body_is_value = sum(1 for c in body_cells if _is_value_like(c)) / len(body_cells) >= 0.7
        if not body_is_value:
            continue
        testifying += 1
        candidate = grid[index][col] if col < width else None
        if _cell_text(candidate) and not _is_value_like(candidate):
            agreeing += 1
    if testifying == 0:
        return 0.0, 0
    return agreeing / testifying, testifying


def _header_score(grid: Grid, index: int, body_width: int) -> float:
    """How much row `index` looks like the header of the rows beneath it."""
    row = grid[index]
    fill = _row_fill(row)
    if fill == 0:
        return 0.0

    # 1. Completeness relative to the table's real width.
    completeness = min(1.0, fill / body_width) if body_width else 0.0

    # 2. Label-ness: how few of its own cells parse as data.
    non_empty = [c for c in row if _cell_text(c)]
    label_ratio = sum(1 for c in non_empty if not _is_value_like(c)) / len(non_empty)

    # 3. Distinctness: a header rarely repeats a label.
    texts = [_cell_text(c).lower() for c in non_empty]
    distinct_ratio = len(set(texts)) / len(texts)

    contrast, testifying = _column_contrast(grid, index)
    if testifying:
        # There ARE typed columns to compare against, so contrast is decisive:
        # no contrast means no header, whatever the other signals say.
        if contrast == 0.0:
            return 0.0
        return 0.20 * completeness + 0.15 * label_ratio + 0.55 * contrast + 0.10 * distinct_ratio

    # An all-text table gives no type contrast to work with. Fall back to the
    # weaker signals — the cost of being wrong here is low, since the first row
    # of an all-text table is a header far more often than not.
    return 0.45 * completeness + 0.35 * label_ratio + 0.20 * distinct_ratio


def _body_width(grid: Grid) -> int:
    """The table's real column count: the most common non-empty cell count.

    Using the mode rather than the max keeps a stray note in an outlying cell
    from inflating the width of the whole table.
    """
    counts: dict[int, int] = {}
    for row in grid:
        fill = _row_fill(row)
        if fill:
            counts[fill] = counts.get(fill, 0) + 1
    if not counts:
        return 0
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def detect_header(grid: Grid) -> tuple[int | None, float]:
    """Index of the best header row, plus its score. None when nothing fits."""
    if not grid:
        return None, 0.0
    width = _body_width(grid)
    limit = min(len(grid) - 1, MAX_HEADER_SCAN)
    best_index, best_score = None, 0.0
    for i in range(max(limit, 1)):
        score = _header_score(grid, i, width)
        if score > best_score:
            best_index, best_score = i, score
    # A single-row table has no body to contrast against; treat its only row
    # as the header if it reads like labels.
    if best_index is None and len(grid) == 1:
        row = grid[0]
        non_empty = [c for c in row if _cell_text(c)]
        if non_empty and all(not _is_value_like(c) for c in non_empty):
            return 0, 0.5
    return best_index, best_score


def _has_group_header_above(grid: Grid, header_index: int) -> bool:
    """True when the row ABOVE the detected header is its top half.

    A merged two-row header looks like:

        2024 |      | 2025 |          ← spans, written as value-then-blanks
        Q1   | Q2   | Q1   | Q2       ← the row that scores as the header

    Scoring picks the lower row (it's the complete, label-like one), so the
    grouping row has to be recognised by looking up. Two conditions keep a
    report *title* from being swallowed the same way: the row must have at
    least two non-empty cells (a title is one), and it must be sparser than the
    header (a second full row of labels is a data row, not a span).
    """
    if header_index == 0:
        return False
    above = grid[header_index - 1]
    fill_above, fill_header = _row_fill(above), _row_fill(grid[header_index])
    if fill_above < 2 or fill_above >= fill_header:
        return False
    # The first cell must start the first span; a right-aligned note above the
    # table is not a group header.
    first_filled = next((i for i, c in enumerate(above) if _cell_text(c)), None)
    return first_filled == 0


def _merge_header_rows(top: list[Cell], bottom: list[Cell]) -> list[str]:
    """Flatten a two-row header, carrying the top label across its span.

    Excel writes a horizontally-merged cell as a value in the first column of
    the span and blanks after it, so forward-filling reconstructs the grouping.
    """
    merged: list[str] = []
    carried = ""
    for i in range(max(len(top), len(bottom))):
        top_text = _cell_text(top[i]) if i < len(top) else ""
        bottom_text = _cell_text(bottom[i]) if i < len(bottom) else ""
        if top_text:
            carried = top_text
        parts = [p for p in (carried, bottom_text) if p]
        merged.append(" ".join(parts) if parts else "")
    return merged


# ---------------------------------------------------------------------------
# Layout arbitration (the model's only say in shaping)
# ---------------------------------------------------------------------------


def arbitration_excerpt(grid: Grid) -> Grid:
    """The bounded slice of the grid an arbiter is shown."""
    return [
        [
            (_cell_text(c)[:MAX_ARBITRATION_CELL] or None)
            for c in row[:MAX_ARBITRATION_COLS]
        ]
        for row in grid[:MAX_ARBITRATION_ROWS]
    ]


def _arbitrate(arbiter: LayoutArbiter, grid: Grid) -> int | None:
    """Ask, then check. A model answer is a suggestion, not an instruction.

    Rejected if it is out of range or would leave no data rows — either would
    turn a table we could still salvage with generated names into nothing at
    all, which is strictly worse than admitting low confidence.
    """
    try:
        verdict = arbiter(arbitration_excerpt(grid))
    except Exception as exc:  # noqa: BLE001 - deliberate boundary
        logger.warning("layout arbitration failed: %s", exc)
        return None
    if verdict is None:
        return None
    if not isinstance(verdict, int) or not 0 <= verdict < len(grid) - 1:
        logger.warning("layout arbitration returned an unusable row: %r", verdict)
        return None
    return verdict


# ---------------------------------------------------------------------------
# Footer detection
# ---------------------------------------------------------------------------


def _strip_footer_rows(rows: Grid) -> tuple[Grid, int]:
    """Drop trailing summary rows ("TOTAL", "Promedio", …).

    Only from the bottom, and only while they keep matching: a legitimate row
    for a customer literally named "Total S.A." in the middle of the table is
    left alone.
    """
    removed = 0
    while rows:
        last = rows[-1]
        texts = [_cell_text(c) for c in last if _cell_text(c)]
        if not texts:
            rows = rows[:-1]
            removed += 1
            continue
        labels = [t for t in texts if _TOTAL_TOKENS.match(t)]
        if labels:
            # The discriminator is what the OTHER cells hold. A totals line is
            # a label plus numbers; a customer called "Total S.A." still has
            # its city, its status, its salesperson — text in other columns.
            others = [t for t in texts if t not in labels]
            all_others_numeric = bool(others) and all(
                parse_number(t) is not None for t in others
            )
            sparse = _row_fill(last) <= max(1, len(last) // 2)
            if all_others_numeric or sparse:
                rows = rows[:-1]
                removed += 1
                continue
        break
    return rows, removed


# ---------------------------------------------------------------------------
# Column naming
# ---------------------------------------------------------------------------


def normalise_name(raw: str, fallback_index: int) -> str:
    """A safe, readable column identifier.

    Accents are folded and spaces become underscores so the name is usable in
    generated SQL without quoting, which keeps Module A's output legible.
    """
    text = unicodedata.normalize("NFKD", raw or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.strip().lower()
    text = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text or text.isdigit():
        return f"column_{fallback_index + 1}"
    if text[0].isdigit():
        text = f"col_{text}"
    return text[:63]


def dedupe_names(names: list[str]) -> list[str]:
    """Make names unique, preserving the first occurrence."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in names:
        if name not in seen:
            seen[name] = 1
            out.append(name)
        else:
            seen[name] += 1
            out.append(f"{name}_{seen[name]}")
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def shape_table(
    raw: RawTable,
    min_header_score: float = 0.45,
    arbiter: LayoutArbiter | None = None,
) -> ShapedTable | None:
    """Grid → header + rows. None when there's nothing usable in it.

    `arbiter`, when given, is consulted **only** where the rules already failed
    — the deterministic path is never overridden by a model. That ordering is
    the whole design: it means a model outage or a nonsense answer degrades to
    exactly the behaviour we have without one.
    """
    grid = _drop_empty_rows(_rectangularise(raw.grid))
    grid, dropped_cols = _drop_empty_columns(grid)
    if not grid:
        return None

    notes: list[str] = []
    if dropped_cols:
        notes.append(
            f"Se ignoraron {dropped_cols} columnas vacías."
            if dropped_cols > 1
            else "Se ignoró 1 columna vacía."
        )

    header_index, score = detect_header(grid)
    synthesised = False
    arbitrated = False
    confidence = InferenceConfidence.HIGH

    if arbiter is not None and (header_index is None or score < min_header_score):
        # The rules couldn't settle it: an ambiguous layout.
        verdict = _arbitrate(arbiter, grid)
        if verdict is not None:
            header_index, score, arbitrated = verdict, min_header_score, True

    if header_index is None or score < min_header_score:
        # No convincing header. Rather than promoting the first row of real
        # data to a header (which loses a record AND produces nonsense names),
        # generate names and say so.
        # English, like every other generated identifier: these become real
        # column names, and Module A's Text-to-SQL model links the question to
        # them.
        header = [f"column_{i + 1}" for i in range(len(grid[0]))]
        body = grid
        synthesised = True
        confidence = InferenceConfidence.LOW
        notes.append(
            "No encontramos una fila de encabezados, así que nombramos las columnas "
            "automáticamente. Renombralas si hace falta."
        )
    else:
        dropped_before = header_index
        if _has_group_header_above(grid, header_index):
            header_cells = _merge_header_rows(grid[header_index - 1], grid[header_index])
            dropped_before = header_index - 1
            notes.append("El encabezado ocupaba dos filas; las unimos.")
            confidence = InferenceConfidence.MEDIUM
        else:
            header_cells = [_cell_text(c) for c in grid[header_index]]
        body = grid[header_index + 1 :]
        header = list(header_cells)
        if dropped_before > 0:
            notes.append(
                f"Se descartaron {dropped_before} filas anteriores al encabezado."
                if dropped_before > 1
                else "Se descartó 1 fila anterior al encabezado."
            )
        if score < 0.65:
            confidence = InferenceConfidence.MEDIUM
        if arbitrated:
            # Never `HIGH`: the rules found nothing convincing, so the user is
            # the one who should have the last look.
            confidence = InferenceConfidence.MEDIUM
            notes.append(
                "La estructura del archivo era ambigua; leímos esta fila como "
                "encabezado. Revisala."
            )

    body, footer_rows = _strip_footer_rows(body)
    if footer_rows:
        notes.append(
            f"Se descartaron {footer_rows} filas de totales al final."
            if footer_rows > 1
            else "Se descartó una fila de totales al final."
        )

    if not body:
        return None

    names = dedupe_names([normalise_name(h, i) for i, h in enumerate(header)])
    # Pad or trim every row to the header width so downstream code can assume
    # rectangularity.
    width = len(names)
    rows: Grid = [list(r[:width]) + [None] * max(0, width - len(r)) for r in body]

    return ShapedTable(
        header=names,
        rows=rows,
        origin=raw.origin,
        header_confidence=InferenceConfidence.LOW if synthesised else confidence,
        notes=notes,
        header_synthesised=synthesised,
    )


__all__ = [
    "MAX_ARBITRATION_ROWS",
    "MAX_HEADER_SCAN",
    "LayoutArbiter",
    "arbitration_excerpt",
    "dedupe_names",
    "detect_header",
    "normalise_name",
    "shape_table",
]
