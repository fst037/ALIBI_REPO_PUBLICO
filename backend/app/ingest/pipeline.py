"""The ingestion pipeline: raw bytes in, named typed tables out.

    detect → extract → shape → profile → name

This module is pure. It touches no database, no S3 and no network — it takes
bytes and returns an `IngestOutcome`. Persisting the result is the caller's
job (the local thread runner or the stage-2 committer Lambda), which is what
makes the whole thing testable offline and identical in both deployments.

Semantic enrichment is a separate, optional step (`app.ingest.inference`) that
runs *after* this one and only rewrites names and descriptions. With the
provider set to `none` this pipeline alone produces a complete, queryable
dataset — the model makes it nicer, not possible.
"""

from __future__ import annotations

import re
import unicodedata

from app.ingest import extract as extractors
from app.ingest.detect import detect_file_type
from app.ingest.errors import IngestError, NoTablesFound, TooManyRows
from app.ingest.models import DetectedTable, IngestOutcome, TypedTable
from app.ingest.profile import profile_table
from app.ingest.shape import LayoutArbiter, shape_table
from app.schemas.datasets import DatasetFileType

# Tables smaller than this are almost always a caption, a legend, or a stray
# key/value block that the island detector couldn't tell from a table.
MIN_USEFUL_ROWS = 1
MIN_USEFUL_COLUMNS = 2


def slugify(text: str, fallback: str = "table") -> str:
    """Fold arbitrary text into a safe SQL identifier.

    `fallback` is English on purpose: it becomes a real table name whenever the
    source text slugifies to nothing or starts with a digit, and generated
    identifiers have to read the same way as every other one Module A sees.
    """
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = re.sub(r"[^\w]+", "_", folded.strip().lower(), flags=re.UNICODE)
    folded = re.sub(r"_+", "_", folded).strip("_")
    if not folded or folded[0].isdigit():
        folded = f"{fallback}_{folded}" if folded else fallback
    return folded[:48]


def _base_name(filename: str) -> str:
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return slugify(stem)


def _table_name(base: str, table: TypedTable, index: int, total: int) -> str:
    """Name a detected table.

    With one table the file's own name is the obvious choice. With several,
    each needs to be distinguishable at a glance in the confirmation screen —
    the sheet name is far more meaningful than `_2`, so it wins when present.
    """
    if total == 1:
        return base
    origin = table.origin
    if origin.sheet:
        suffix = slugify(origin.sheet)
        name = f"{base}_{suffix}" if suffix != base else base
        return name if origin.index == 0 else f"{name}_{origin.index + 1}"
    if origin.page:
        return f"{base}_p{origin.page}" + (f"_{origin.index + 1}" if origin.index else "")
    return f"{base}_{index + 1}"


def run_pipeline(
    filename: str,
    data: bytes,
    *,
    max_rows: int | None = None,
    file_type: DatasetFileType | None = None,
    arbiter: LayoutArbiter | None = None,
) -> IngestOutcome:
    """Bytes → detected tables. Raises `IngestError` for anything unusable.

    `arbiter` is the one hook a model gets into this otherwise-pure function,
    and it is passed in rather than looked up so the purity survives: this
    module still performs no I/O of its own. `shape_table` consults it only
    where the deterministic rules already came up empty.
    """
    resolved_type = file_type or detect_file_type(filename, data)
    raw_tables = extractors.extract(resolved_type, data)

    outcome = IngestOutcome()
    base = _base_name(filename)

    shaped_tables = []
    for raw in raw_tables:
        shaped = shape_table(raw, arbiter=arbiter)
        if shaped is None:
            continue
        if len(shaped.rows) < MIN_USEFUL_ROWS or len(shaped.header) < MIN_USEFUL_COLUMNS:
            continue
        shaped_tables.append(shaped)

    if not shaped_tables:
        raise NoTablesFound(f"{len(raw_tables)} raw block(s) extracted, none usable")

    typed_tables = [profile_table(s) for s in shaped_tables]

    if max_rows is not None:
        for typed in typed_tables:
            if typed.row_count > max_rows:
                raise TooManyRows(f"{typed.row_count} rows exceeds the limit of {max_rows}")

    total = len(typed_tables)
    for index, typed in enumerate(typed_tables):
        outcome.tables.append(
            DetectedTable(
                typed=typed,
                suggested_name=_table_name(base, typed, index, total),
                source_label=typed.origin.label(),
            )
        )
        outcome.warnings.extend(typed.notes)

    if total > 1:
        outcome.warnings.insert(
            0,
            f"Encontramos {total} tablas en el archivo. Revisá y confirmá cada una.",
        )
    return outcome


def safe_run_pipeline(filename: str, data: bytes, **kwargs) -> tuple[IngestOutcome | None, IngestError | None]:
    """`run_pipeline`, with failures returned rather than raised.

    The worker needs to record *why* an upload failed in a form it can show the
    user, and an unexpected exception must not look different from a known one:
    both become an `IngestError` with a plain-language message.
    """
    try:
        return run_pipeline(filename, data, **kwargs), None
    except IngestError as exc:
        return None, exc
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all at the boundary
        return None, IngestError(f"unexpected {type(exc).__name__}: {exc}")


__all__ = ["run_pipeline", "safe_run_pipeline", "slugify"]
