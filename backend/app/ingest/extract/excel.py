"""Excel extraction, including finding several tables inside one sheet.

A spreadsheet is a canvas, not a table. People put a summary block in the top
left, a detail table below it, and a lookup table off to the right — all on one
sheet, separated by nothing but blank rows and columns. `pandas.read_excel`
reads that canvas as a single ragged rectangle: the result has the summary's
headers, the detail's rows, and a lot of NaN.

So this module finds **islands** — maximal blocks of populated cells separated
from each other by at least one fully-blank row *and* column — and returns each
as its own table. That is what makes "we found 3 tables in this file" possible
instead of one unusable mess.

It also reads merged-cell ranges from openpyxl, which pandas discards. A
horizontally merged cell is how Excel writes a two-row grouped header, and
un-merging it (writing the value into every cell of its span) is what lets
`shape` reconstruct `2024 Q1 / 2024 Q2`.
"""

from __future__ import annotations

import io

from app.ingest.errors import FileUnreadable
from app.ingest.models import Cell, Grid, RawTable, TableOrigin

# An island smaller than this is a stray note, a page number, or a signature
# line — not a table worth showing the user as a dataset.
MIN_ISLAND_ROWS = 2
MIN_ISLAND_COLS = 2
# Sheets bigger than this are truncated; a spreadsheet with a million rows is
# past the point where this product is the right tool anyway.
MAX_ROWS = 200_000
MAX_COLS = 512


def _cell_to_text(value: object) -> Cell:
    """Normalise a cell to text, keeping what the user would recognise.

    Dates come back from openpyxl as datetimes; rendering them ISO keeps them
    parseable downstream without imposing a locale here.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, bool):
        return "true" if value else "false"
    # datetime/date/time — isoformat is unambiguous and round-trips.
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        text = iso()
        # Midnight almost always means "a date", not "a timestamp".
        return text[:10] if text.endswith("T00:00:00") else text
    return str(value)


def _read_sheet(sheet) -> Grid:  # noqa: ANN001 - openpyxl worksheet
    grid: Grid = []
    for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
        if row_index >= MAX_ROWS:
            break
        grid.append([_cell_to_text(v) for v in row[:MAX_COLS]])
    return grid


def _unmerge(sheet, grid: Grid) -> None:  # noqa: ANN001 - openpyxl worksheet
    """Write a merged cell's value into every position of its span.

    openpyxl reports the value only at the top-left of the range and `None`
    everywhere else, which is indistinguishable from a genuinely blank cell —
    and that difference is exactly what tells a grouped header from a title.
    """
    for merged in list(getattr(sheet, "merged_cells", {}).ranges or []):
        r0, c0 = merged.min_row - 1, merged.min_col - 1
        if r0 >= len(grid) or c0 >= len(grid[r0]):
            continue
        value = grid[r0][c0]
        if value is None:
            continue
        for r in range(r0, min(merged.max_row, len(grid))):
            for c in range(c0, min(merged.max_col, len(grid[r]))):
                if grid[r][c] is None:
                    grid[r][c] = value


def _blank_row(grid: Grid, index: int) -> bool:
    return not any(c for c in grid[index])


def _blank_column(grid: Grid, index: int, rows: range) -> bool:
    return not any(
        grid[r][index] for r in rows if index < len(grid[r])
    )


def _row_bands(grid: Grid) -> list[tuple[int, int]]:
    """Split the sheet into vertical bands separated by blank rows."""
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for i in range(len(grid)):
        if _blank_row(grid, i):
            if start is not None:
                bands.append((start, i))
                start = None
        elif start is None:
            start = i
    if start is not None:
        bands.append((start, len(grid)))
    return bands


def _column_bands(grid: Grid, rows: range) -> list[tuple[int, int]]:
    """Within a row band, split into horizontal bands separated by blank columns."""
    width = max((len(grid[r]) for r in rows), default=0)
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for c in range(width):
        if _blank_column(grid, c, rows):
            if start is not None:
                bands.append((start, c))
                start = None
        elif start is None:
            start = c
    if start is not None:
        bands.append((start, width))
    return bands


def _row_bands_in_columns(grid: Grid, rows: range, cols: range) -> list[tuple[int, int]]:
    """Row bands within `rows`, but "blank" means blank across only `cols`.

    Exists for exactly one situation: several tables stacked vertically in a
    narrow column band, sitting beside a *taller* table in the neighbouring
    band. `_row_bands` (used to find `rows` in the first place) checks every
    column, so a row is "blank" there only once every band on the sheet has
    run out of data — which for the short stack never happens, because the
    tall neighbour is still going. The whole stack then reads as one
    unbroken island, and only its first header survives; everything below
    becomes garbage rows under it. Re-checking blankness against just this
    island's own columns is what recovers the vertical breaks the global
    check couldn't see.
    """
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for i in rows:
        row = grid[i]
        blank = not any(row[c] for c in cols if c < len(row))
        if blank:
            if start is not None:
                bands.append((start, i))
                start = None
        elif start is None:
            start = i
    if start is not None:
        bands.append((start, rows.stop))
    return bands


def find_islands(grid: Grid) -> list[tuple[int, int, int, int]]:
    """Maximal populated blocks, as (row_start, row_end, col_start, col_end).

    Three passes, not two: split on blank rows, split each band on blank
    columns, then re-split each of *those* on blank rows again — this time
    counting only its own columns as evidence. The first row-split works over
    every column, so a band that looks unbroken there can still be several
    tables stacked in one narrow column range, kept "joined" only because a
    taller table next door has data in the same rows (see
    `_row_bands_in_columns`). Re-checking narrows the blank-row test to
    exactly the columns this island actually occupies, which is what a
    two-pass row→column split can never see on its own.
    """
    islands: list[tuple[int, int, int, int]] = []
    for r0, r1 in _row_bands(grid):
        rows = range(r0, r1)
        for c0, c1 in _column_bands(grid, rows):
            cols = range(c0, c1)
            for sr0, sr1 in _row_bands_in_columns(grid, rows, cols):
                height, width = sr1 - sr0, c1 - c0
                if height >= MIN_ISLAND_ROWS and width >= MIN_ISLAND_COLS:
                    islands.append((sr0, sr1, c0, c1))
    return islands


def _slice(grid: Grid, bounds: tuple[int, int, int, int]) -> Grid:
    r0, r1, c0, c1 = bounds
    out: Grid = []
    for r in range(r0, r1):
        row = grid[r]
        out.append([row[c] if c < len(row) else None for c in range(c0, c1)])
    return out


def extract_excel(data: bytes) -> list[RawTable]:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise FileUnreadable("openpyxl not installed") from exc

    try:
        # read_only would be faster but does not expose merged ranges, which
        # this module specifically needs. data_only reads cached formula
        # results — a formula's text is useless as data.
        workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:  # openpyxl raises a zoo of exception types
        raise FileUnreadable(f"openpyxl could not open the workbook: {exc}") from exc

    tables: list[RawTable] = []
    for sheet in workbook.worksheets:
        if getattr(sheet, "sheet_state", "visible") != "visible":
            # Hidden sheets are almost always scratch work or lookup data the
            # author deliberately put out of sight.
            continue
        grid = _read_sheet(sheet)
        if not grid:
            continue
        _unmerge(sheet, grid)
        islands = find_islands(grid)
        for index, bounds in enumerate(islands):
            tables.append(
                RawTable(
                    grid=_slice(grid, bounds),
                    origin=TableOrigin(sheet=sheet.title, index=index),
                )
            )
    workbook.close()
    return tables


__all__ = ["MIN_ISLAND_COLS", "MIN_ISLAND_ROWS", "extract_excel", "find_islands"]
