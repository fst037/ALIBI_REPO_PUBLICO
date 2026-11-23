"""PDF and Word extraction.

Per the Module B spec, ALIBI *integrates* Document AI rather than advancing it:
this uses pdfplumber's two table strategies off the shelf and stops there. A
PDF with no text layer (a scan) is reported honestly as unsupported instead of
being run through OCR — LayoutLMv3/Donut are explicitly deferred to future work,
and silently returning garbage from a bad OCR pass would be worse than saying no.

The one piece of real logic here is **stitching tables across pages**. A report
whose table runs over four pages yields four separate extractions, each with
the header repeated at the top (or not repeated at all). Returning them as four
datasets would be wrong — it's one table — so consecutive page tables that
agree on column count are merged, dropping a repeated header row when present.
"""

from __future__ import annotations

import io

from app.ingest.errors import FileUnreadable, ScannedPdfUnsupported
from app.ingest.models import Cell, Grid, RawTable, TableOrigin

MAX_PAGES = 200
# Below this, the "text" a PDF yields is page furniture (numbers, headers) and
# the document is effectively an image.
MIN_TEXT_CHARS_PER_PAGE = 20


def _clean_cell(value: object) -> Cell:
    if value is None:
        return None
    text = str(value).replace("\n", " ").strip()
    return text or None


def _normalise(rows: list[list[object]]) -> Grid:
    return [[_clean_cell(c) for c in row] for row in rows]


def _same_header(a: Grid, b: Grid) -> bool:
    """True when b starts with a repeat of a's first row."""
    if not a or not b:
        return False
    first_a = [(c or "").strip().lower() for c in a[0]]
    first_b = [(c or "").strip().lower() for c in b[0]]
    return first_a == first_b and any(first_a)


def stitch_pages(page_tables: list[tuple[int, Grid]]) -> list[RawTable]:
    """Merge consecutive same-width tables into one logical table.

    Width equality is the join condition: a table continuing onto the next page
    keeps its columns, while a genuinely different table almost always has a
    different count. When the continuation repeats the header, that row is
    dropped rather than becoming a data row in the middle of the table.
    """
    merged: list[tuple[int, Grid]] = []
    for page, grid in page_tables:
        if not grid:
            continue
        if merged:
            _, previous = merged[-1]
            prev_width = max((len(r) for r in previous), default=0)
            width = max((len(r) for r in grid), default=0)
            if prev_width == width and prev_width > 1:
                continuation = grid[1:] if _same_header(previous, grid) else grid
                merged[-1] = (merged[-1][0], previous + continuation)
                continue
        merged.append((page, grid))

    out: list[RawTable] = []
    counts: dict[int, int] = {}
    for page, grid in merged:
        index = counts.get(page, 0)
        counts[page] = index + 1
        out.append(RawTable(grid=grid, origin=TableOrigin(page=page, index=index)))
    return out


def extract_pdf(data: bytes) -> list[RawTable]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise FileUnreadable("pdfplumber not installed") from exc

    page_tables: list[tuple[int, Grid]] = []
    text_chars = 0
    pages_read = 0

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page_number, page in enumerate(pdf.pages[:MAX_PAGES], start=1):
                pages_read += 1
                text_chars += len(page.extract_text() or "")
                # "lattice" — tables drawn with ruling lines. Tried first
                # because when the lines exist they are unambiguous.
                found = page.extract_tables(
                    {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
                )
                if not found:
                    # "stream" — inferred from whitespace alignment. Necessary
                    # for the many reports that draw no borders at all.
                    found = page.extract_tables(
                        {"vertical_strategy": "text", "horizontal_strategy": "text"}
                    )
                for table in found:
                    grid = _normalise(table)
                    if len(grid) >= 2 and max((len(r) for r in grid), default=0) >= 2:
                        page_tables.append((page_number, grid))
    except Exception as exc:
        raise FileUnreadable(f"pdfplumber could not read the document: {exc}") from exc

    if not page_tables:
        # Distinguish "no text at all" (a scan — a permanent, explainable
        # failure) from "text but no tabular structure" (prose), because the
        # advice to the user differs.
        if pages_read and text_chars < MIN_TEXT_CHARS_PER_PAGE * pages_read:
            raise ScannedPdfUnsupported(
                f"{text_chars} chars of text across {pages_read} pages"
            )
        return []

    return stitch_pages(page_tables)


def extract_docx(data: bytes) -> list[RawTable]:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise FileUnreadable("python-docx not installed") from exc

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise FileUnreadable(f"python-docx could not read the document: {exc}") from exc

    tables: list[RawTable] = []
    for index, table in enumerate(document.tables):
        grid: Grid = [[_clean_cell(cell.text) for cell in row.cells] for row in table.rows]
        if len(grid) >= 2 and max((len(r) for r in grid), default=0) >= 2:
            tables.append(RawTable(grid=grid, origin=TableOrigin(index=index)))
    return tables


__all__ = ["extract_docx", "extract_pdf", "stitch_pages"]
