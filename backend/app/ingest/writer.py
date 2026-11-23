"""Serialise a typed table to Parquet bytes.

Bytes, not a file path or an S3 URL: the writer produces a buffer and hands it
to the `ObjectStore`, which is what keeps local-disk and S3 modes on one code
path. Letting pyarrow write to `s3://` directly would mean the processor needed
its own S3 credentials and its own retry behaviour, duplicating the store.

Snappy compression rather than zstd: it is Parquet's most universally supported
codec, and DuckDB decompresses it fast enough that the difference is invisible
at these table sizes.
"""

from __future__ import annotations

import io
import json
import os
from typing import TYPE_CHECKING

from app.ingest.models import TypedTable

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

# pyarrow's manylinux wheels default to jemalloc, which lazily spawns its own
# background maintenance thread on the first real allocation. This module is
# the only place in the app that imports pyarrow, and `to_parquet_bytes` runs
# inside the ThreadRunner's worker thread (app/ingest/jobs.py) — so that first
# allocation happens off the main thread, which is exactly the condition a
# known class of Arrow/jemalloc segfaults needs. `nthreads=1` below did *not* fix it — the crash is in
# `pa.Table.from_pandas`'s single-threaded column-conversion path, not its
# optional parallel one. Forcing the system allocator sidesteps jemalloc
# entirely; it must be set before pyarrow is imported anywhere in the
# process, hence the module-level placement.
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

COMPRESSION = "snappy"


def to_parquet_bytes(frame: "pd.DataFrame") -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    # Kept from the earlier (insufficient, on its own) fix: these tables are
    # small enough that single-threaded conversion costs nothing measurable.
    table = pa.Table.from_pandas(frame, preserve_index=False, nthreads=1)
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression=COMPRESSION)
    return buffer.getvalue()


def profile_to_json(table: TypedTable, table_name: str) -> bytes:
    """The sidecar profile, stored next to the Parquet.

    Persisted so a re-inference (or a later swap to the trained adapter) can
    run without re-parsing the original file — which by then has been deleted
    on purpose.
    """
    payload = {
        "tableName": table_name,
        "sourceLabel": table.origin.label(),
        "rowCount": table.row_count,
        "notes": table.notes,
        "columns": [
            {
                "name": c.name,
                "sourceName": c.source_name,
                "ordinal": c.ordinal,
                "type": c.column_type.value,
                "sqlType": c.sql_type,
                "vlType": c.vl_type.value,
                "confidence": c.confidence.value,
                "nRows": c.n_rows,
                "nUnique": c.n_unique,
                "nNull": c.n_null,
                "nullRate": round(c.null_rate, 4),
                "samples": c.samples,
                "min": c.min_value,
                "max": c.max_value,
                "mean": c.mean_value,
                "isPrimaryKeyCandidate": c.is_primary_key_candidate,
            }
            for c in table.columns
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


__all__ = ["COMPRESSION", "profile_to_json", "to_parquet_bytes"]
