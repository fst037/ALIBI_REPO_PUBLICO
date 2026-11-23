"""CSV / TSV / delimited text, and JSON.

Delimited files get one table each — a text file holding two tables separated
by a blank line does happen, but far too rarely to justify the false positives
that splitting on blank rows would cause in a legitimate file with an empty
record.

The work here is not the parsing (`csv` handles quoting and embedded newlines
correctly); it's getting the *delimiter* and *encoding* right first, which is
`detect`'s job, and tolerating rows with the wrong number of fields, which the
shaper then pads.
"""

from __future__ import annotations

import csv
import io
import json

from app.ingest.detect import decode_text, sniff_delimiter
from app.ingest.errors import EmptyFile, FileUnreadable
from app.ingest.models import Grid, RawTable, TableOrigin

# csv's default field limit is 128 KB; a cell holding a pasted document blows
# past it and raises. Raising the limit is friendlier than failing the upload.
csv.field_size_limit(10 * 1024 * 1024)

MAX_ROWS = 1_000_000


def extract_delimited(data: bytes) -> list[RawTable]:
    decoded = decode_text(data)
    if not decoded.text.strip():
        raise EmptyFile("file decoded to whitespace only")

    guess = sniff_delimiter(decoded.text)
    try:
        reader = csv.reader(io.StringIO(decoded.text), delimiter=guess.delimiter)
        grid: Grid = []
        for i, row in enumerate(reader):
            if i >= MAX_ROWS:
                break
            grid.append([c if c.strip() else None for c in row])
    except csv.Error as exc:
        raise FileUnreadable(f"csv parse failed: {exc}") from exc

    if not grid:
        raise EmptyFile("no rows parsed")
    return [RawTable(grid=grid, origin=TableOrigin())]


def _flatten(obj: object, prefix: str = "") -> dict[str, object]:
    """Flatten nested objects to `parent_child` keys.

    Arrays are left as their JSON text rather than exploded into columns:
    exploding changes the row count, which would silently turn 100 orders into
    340 rows of line items without the user asking for it.
    """
    out: dict[str, object] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            name = f"{prefix}_{key}" if prefix else str(key)
            if isinstance(value, dict):
                out.update(_flatten(value, name))
            elif isinstance(value, list):
                out[name] = json.dumps(value, ensure_ascii=False)
            else:
                out[name] = value
    else:
        out[prefix or "valor"] = obj
    return out


def _records_to_grid(records: list[object]) -> Grid:
    flattened = [_flatten(r) for r in records]
    # Union of keys, in first-seen order — records in the wild are not
    # uniformly shaped, and dropping a key because record 1 lacked it loses
    # data.
    columns: list[str] = []
    for row in flattened:
        for key in row:
            if key not in columns:
                columns.append(key)
    grid: Grid = [list(columns)]
    for row in flattened:
        grid.append(
            [
                None if row.get(c) is None else str(row.get(c))
                for c in columns
            ]
        )
    return grid


def extract_json(data: bytes) -> list[RawTable]:
    decoded = decode_text(data)
    text = decoded.text.strip()
    if not text:
        raise EmptyFile("file decoded to whitespace only")

    records: list[object] | None = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # JSON Lines: one object per line.
        try:
            records = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise FileUnreadable(f"not valid JSON or JSONL: {exc}") from exc
    else:
        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict):
            # A common API shape: {"data": [...]} / {"results": [...]}. Take the
            # longest list value; if there is none, treat the object as one row.
            list_values = [v for v in parsed.values() if isinstance(v, list) and v]
            records = max(list_values, key=len) if list_values else [parsed]
        else:
            records = [parsed]

    if not records:
        raise EmptyFile("JSON contained no records")
    return [RawTable(grid=_records_to_grid(records), origin=TableOrigin())]


__all__ = ["extract_delimited", "extract_json"]
