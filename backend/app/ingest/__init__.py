"""Module B — Zero-ETL ingestion.

A file the user uploaded becomes one or more queryable tables. The stages are
deliberately separate and individually testable:

    detect        what format, encoding and delimiter is this?
    extract/      bytes → rectangles of text (per format)
    shape         rectangle → header + rows (titles, junk and totals removed)
    typing_rules  column → type (es-AR numbers and dates included)
    profile       typed values → statistics, key candidates, model prompt
    writer        typed table → Parquet bytes
    storage       where those bytes live (local disk or S3)
    pipeline      the whole chain, as one pure function

`pipeline.run_pipeline` touches no database, no network and no AWS: bytes in,
`IngestOutcome` out. Persisting the result belongs to the caller — the local
thread runner during development, the stage-2 committer Lambda in production —
which is what lets the same code serve both and be tested with neither.

The semantic layer (renaming `fch_vcto` to `fecha_vencimiento`, describing
columns, embedding them) is a separate, optional step. With no model
configured this pipeline still produces a complete, queryable dataset.
"""

from app.ingest.errors import IngestError
from app.ingest.models import DetectedTable, IngestOutcome
from app.ingest.pipeline import run_pipeline, safe_run_pipeline

__all__ = [
    "DetectedTable",
    "IngestError",
    "IngestOutcome",
    "run_pipeline",
    "safe_run_pipeline",
]
