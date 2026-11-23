"""Format-specific extractors: bytes → a list of raw cell grids.

Every extractor returns `list[RawTable]` — plural, because "one file, one
table" is not true of the files real users upload. A spreadsheet often holds
one table per sheet, sometimes several per sheet; a PDF report can carry a
table on each of five pages.

Nothing here interprets the cells. Extraction finds rectangles of text and
records where they came from; deciding which row is the header and what type
each column is belongs to `shape` and `typing_rules`. Keeping that boundary
sharp is what lets the same shaping logic serve CSV, Excel, PDF and Word.
"""

from __future__ import annotations

from app.ingest.errors import UnsupportedFileType
from app.ingest.models import RawTable
from app.schemas.datasets import DatasetFileType


def extract(file_type: DatasetFileType, data: bytes) -> list[RawTable]:
    """Dispatch to the right extractor. Imports are local so that the API
    Lambda — which never parses a file — doesn't pay for pandas/openpyxl/
    pdfplumber at import time."""
    if file_type is DatasetFileType.csv:
        from app.ingest.extract.delimited import extract_delimited

        return extract_delimited(data)
    if file_type is DatasetFileType.json:
        from app.ingest.extract.delimited import extract_json

        return extract_json(data)
    if file_type is DatasetFileType.xlsx:
        from app.ingest.extract.excel import extract_excel

        return extract_excel(data)
    if file_type is DatasetFileType.pdf:
        from app.ingest.extract.document import extract_pdf

        return extract_pdf(data)
    if file_type is DatasetFileType.docx:
        from app.ingest.extract.document import extract_docx

        return extract_docx(data)
    raise UnsupportedFileType(f"no extractor for {file_type}")


__all__ = ["extract"]
