"""The ingestion orchestrator, in the two stages the deployment demands.

    STAGE 1 — compute        STAGE 2 — commit
    ─────────────────        ────────────────
    read raw bytes           read result.json
    run the pipeline         write the control-plane rows
    enrich (model)           (uploads → ready, N × datasets)
    write Parquet
    write result.json
    delete the raw document

The split is not an abstraction for its own sake — it is the VPC boundary.
Stage 1 needs a route to AWS APIs (it calls a model on Bedrock) and must therefore live
*outside* the VPC, where it cannot reach the private RDS. Stage 2 needs the
database and must live *inside*, where it has no internet route. Cutting the
work along that exact seam is what makes the whole pipeline cost $0 in extra
infrastructure instead of $7–32/mo for a NAT gateway or an interface endpoint.

The handoff between them is `result.json` in the object store — the one thing
both halves can reach.

Locally, `process_upload` runs both stages back-to-back in one thread. That is
the same code the two Lambdas run, in the same order, which is what makes the
deployed path testable on a laptop.

The final delete is a product requirement, not an optimisation: ALIBI keeps the
resulting table, never the uploaded document. The raw object survives a
*failure* on purpose, so `POST /uploads/{id}/retry` has something to retry.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from app.core.config import get_settings
from app.ingest.errors import FileUnreadable, IngestError
from app.ingest.inference import column_embeddings, enrich, layout_arbiter
from app.ingest.models import DetectedTable, NewDataset
from app.ingest.pipeline import safe_run_pipeline, slugify
from app.ingest.storage import (
    ObjectStore,
    dataset_parquet_key,
    dataset_profile_key,
    get_object_store,
    raw_upload_key,
    upload_result_key,
    upload_status_key,
)
from app.ingest.writer import profile_to_json, to_parquet_bytes
from app.schemas.datasets import DatasetFileType, DatasetSchema, SchemaColumn
from app.schemas.uploads import UploadPhase

logger = logging.getLogger(__name__)

PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"

# Called as each phase begins. Stage 1 always records the phase in status.json;
# this is the extra hook the local runner uses to also update the control plane,
# which the deployed stage 1 cannot reach.
PhaseSink = Callable[[UploadPhase], None]


def human_size(n_bytes: int) -> str:
    """es-AR byte size: comma decimal separator, matching the fixtures."""
    if n_bytes < 1024:
        return f"{n_bytes} B"
    value = float(n_bytes)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            text = f"{value:.1f}" if value < 100 else f"{value:.0f}"
            return f"{text.replace('.', ',')} {unit}"
    raise AssertionError("unreachable")  # pragma: no cover


def _thousands(n: int) -> str:
    # en-US grouping, matching `lib/format.ts` — this lands in `Dataset.meta`,
    # which the library cards render next to numbers the frontend formatted.
    return f"{n:,}"


def unique_table_name(base: str, taken: set[str]) -> str:
    """A SQL identifier no other table of this owner already uses.

    Suffixed rather than randomised: Module A puts this name in a FROM clause
    and a human reads it in the confirmation screen, so `ventas_2` beats
    `ventas_a3f19c`.
    """
    name = slugify(base)
    if name not in taken:
        taken.add(name)
        return name
    for suffix in range(2, 1000):
        candidate = f"{name}_{suffix}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    candidate = f"{name}_{uuid4().hex[:6]}"  # pragma: no cover - 998 collisions
    taken.add(candidate)
    return candidate


def schema_for(detected: DetectedTable, dataset_id: str) -> DatasetSchema:
    columns = [
        SchemaColumn(
            name=column.name,
            type=column.column_type,
            # Two or three real values say more about a column than any type
            # label — this is what the user actually scans when confirming.
            sampleValues=", ".join(column.samples[:3]),
            included=True,
            confidence=column.confidence,
            # Only meaningful when inference renamed it; identical names would
            # just be noise in the UI.
            sourceName=column.source_name if column.source_name != column.name else None,
            vlType=column.vl_type,
            description=column.description,
            isPrimaryKey=column.is_primary_key_candidate,
        )
        for column in detected.typed.columns
    ]
    return DatasetSchema(
        datasetId=dataset_id,
        rowCount=detected.typed.row_count,
        confidence=detected.confidence,
        columns=columns,
        tableDescription=detected.table_description,
        confirmed=False,
    )


# ---------------------------------------------------------------------------
# The stage-1 → stage-2 handoff
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Stage1Result:
    """What stage 1 leaves in `result.json` for stage 2 to commit.

    Carries an `error` instead of raising, because the two stages are separate
    processes in the deployed topology: a stage-1 exception cannot propagate to
    stage 2, so a failure has to be *data* that stage 2 can act on.
    """

    datasets: list[NewDataset] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> bytes:
        payload = {
            "warnings": self.warnings,
            "error": self.error,
            "datasets": [
                {
                    "id": d.id,
                    "name": d.name,
                    "tableName": d.table_name,
                    "sourceLabel": d.source_label,
                    "storageKey": d.storage_key,
                    "fileType": d.file_type.value,
                    "rowCount": d.row_count,
                    "columnCount": d.column_count,
                    "sizeLabel": d.size_label,
                    "meta": d.meta,
                    "schema": d.schema.model_dump(),
                    "embeddings": d.embeddings,
                    "embeddingModel": d.embedding_model,
                }
                for d in self.datasets
            ],
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> "Stage1Result":
        payload = json.loads(raw.decode("utf-8"))
        return cls(
            warnings=list(payload.get("warnings") or []),
            error=payload.get("error"),
            datasets=[
                NewDataset(
                    id=d["id"],
                    name=d["name"],
                    table_name=d["tableName"],
                    source_label=d["sourceLabel"],
                    storage_key=d["storageKey"],
                    file_type=DatasetFileType(d["fileType"]),
                    row_count=d["rowCount"],
                    column_count=d["columnCount"],
                    size_label=d["sizeLabel"],
                    meta=d["meta"],
                    schema=DatasetSchema(**d["schema"]),
                    embeddings=[list(v) for v in (d.get("embeddings") or [])],
                    embedding_model=d.get("embeddingModel"),
                )
                for d in payload.get("datasets") or []
            ],
        )


def _write_status(
    store: ObjectStore, owner_id: str, upload_id: str, phase: UploadPhase | None, error: str | None = None
) -> None:
    """Progress, where a process with no database can still publish it.

    Best effort on purpose: losing a progress update must not fail an upload
    that is otherwise going fine.
    """
    payload = {"phase": phase.value if phase else None, "error": error}
    try:
        store.put(
            upload_status_key(owner_id, upload_id),
            json.dumps(payload).encode("utf-8"),
            "application/json",
        )
    except Exception as exc:  # noqa: BLE001 - progress is not worth failing over
        logger.warning("could not write status for %s: %s", upload_id, exc)


def read_status(owner_id: str, upload_id: str) -> UploadPhase | None:
    """The phase stage 1 last published. None when unknown."""
    try:
        raw = get_object_store().get(upload_status_key(owner_id, upload_id))
    except Exception:  # noqa: BLE001 - absent or unreadable is simply "unknown"
        return None
    try:
        phase = json.loads(raw.decode("utf-8")).get("phase")
        return UploadPhase(phase) if phase else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Stage 1 — compute. No database.
# ---------------------------------------------------------------------------


def run_stage1(
    owner_id: str,
    upload_id: str,
    filename: str,
    *,
    file_type: DatasetFileType | None = None,
    taken_table_names: set[str] | None = None,
    on_phase: PhaseSink | None = None,
) -> Stage1Result:
    """Bytes in the object store → Parquet + `result.json`. Never raises.

    Touches no database, which is what lets it run outside the VPC and reach a
    model over HTTPS. `taken_table_names` is passed in rather than queried for
    the same reason — the caller that *can* read the control plane supplies it,
    and stage 2 re-checks uniqueness where it actually matters.
    """
    settings = get_settings()
    store = get_object_store()
    raw_key = raw_upload_key(owner_id, upload_id, filename)

    def phase(step: UploadPhase) -> None:
        _write_status(store, owner_id, upload_id, step)
        if on_phase is not None:
            on_phase(step)

    def failed(message: str) -> Stage1Result:
        result = Stage1Result(error=message)
        _write_status(store, owner_id, upload_id, None, message)
        store.put(upload_result_key(owner_id, upload_id), result.to_json(), "application/json")
        return result

    phase(UploadPhase.extracting)
    try:
        data = store.get(raw_key)
    except (FileNotFoundError, OSError) as exc:
        logger.warning("raw object unreadable (upload=%s): %s", upload_id, exc)
        return failed(FileUnreadable().message)

    outcome, error = safe_run_pipeline(
        filename,
        data,
        max_rows=settings.max_rows_per_table,
        file_type=file_type,
        # None unless a provider is configured, in which case the model gets
        # consulted for layouts the rules couldn't settle — and only those.
        arbiter=layout_arbiter(),
    )
    if outcome is None:
        assert error is not None
        logger.info("upload %s rejected: %s", upload_id, error.detail)
        return failed(error.message)

    # Adjacent because profiling happens *inside* `safe_run_pipeline`: the
    # pipeline is one pure blocking call and reporting from within it would mean
    # threading a callback through, which is exactly the I/O it must not do. So
    # `extracting` covers parse-through-profile and this marks its completion.
    phase(UploadPhase.profiling)
    phase(UploadPhase.inferring)
    for detected in outcome.tables:
        enrich(detected)

    phase(UploadPhase.storing)
    taken = set(taken_table_names or set())
    created: list[NewDataset] = []

    try:
        for detected in outcome.tables:
            dataset_id = f"ds_{uuid4().hex[:12]}"
            table_name = unique_table_name(detected.suggested_name, taken)
            parquet = to_parquet_bytes(detected.typed.frame)
            parquet_key = dataset_parquet_key(owner_id, dataset_id)
            store.put(parquet_key, parquet, PARQUET_CONTENT_TYPE)
            store.put(
                dataset_profile_key(owner_id, dataset_id),
                profile_to_json(detected.typed, table_name),
                "application/json",
            )
            vectors, model_id = column_embeddings(detected)
            rows = detected.typed.row_count
            created.append(
                NewDataset(
                    id=dataset_id,
                    name=detected.suggested_name,
                    table_name=table_name,
                    source_label=detected.source_label,
                    storage_key=parquet_key,
                    file_type=file_type or detected_file_type(filename),
                    row_count=rows,
                    column_count=len(detected.typed.columns),
                    size_label=human_size(len(parquet)),
                    meta=f"{_thousands(rows)} rows · {detected.source_label}",
                    schema=schema_for(detected, dataset_id),
                    embeddings=vectors,
                    embedding_model=model_id,
                )
            )
    except IngestError as exc:
        return failed(exc.message)
    except Exception as exc:  # noqa: BLE001 - boundary: a crash must be recorded
        logger.exception("stage 1 crashed for upload %s", upload_id)
        return failed(IngestError(str(exc)).message)

    result = Stage1Result(datasets=created, warnings=list(outcome.warnings))
    store.put(upload_result_key(owner_id, upload_id), result.to_json(), "application/json")
    phase(UploadPhase.done)

    # We keep the table, not the document. Only on success: a failed upload
    # keeps its raw object so `retry` has something to work with.
    try:
        store.delete(raw_key)
    except OSError as exc:  # noqa: BLE001 - a stale raw object is not a failure
        logger.warning("could not delete raw upload %s: %s", raw_key, exc)

    return result


def detected_file_type(filename: str) -> DatasetFileType:
    """Extension-derived type, for the record stage 2 writes.

    Stage 1 already knows the real type from the content, but by the time a
    `NewDataset` is built the only thing left in scope is the name — and the API
    validated the extension before issuing the upload URL.
    """
    from app.ingest.detect import file_type_from_extension

    try:
        return file_type_from_extension(filename)
    except IngestError:
        return DatasetFileType.csv


# ---------------------------------------------------------------------------
# Stage 2 — commit. Database only.
# ---------------------------------------------------------------------------


def commit_stage2(owner_id: str, upload_id: str) -> bool:
    """Read `result.json` and write the control plane. Returns whether it committed.

    Idempotent by intent: SQS delivers at least once, so this may run twice for
    one upload. An upload already `ready` is left alone rather than duplicating
    its datasets.
    """
    from app.services import uploads as uploads_store

    store = get_object_store()
    try:
        raw = store.get(upload_result_key(owner_id, upload_id))
    except (FileNotFoundError, OSError) as exc:
        logger.error("stage 2 found no result for upload %s: %s", upload_id, exc)
        uploads_store.fail(owner_id, upload_id, IngestError().message)
        return False

    try:
        result = Stage1Result.from_json(raw)
    except Exception as exc:  # noqa: BLE001 - a corrupt handoff is a real failure
        logger.exception("stage 2 could not read the result for upload %s", upload_id)
        uploads_store.fail(owner_id, upload_id, IngestError(str(exc)).message)
        return False

    if result.error is not None:
        uploads_store.fail(owner_id, upload_id, result.error)
        return False

    current = uploads_store.get_upload(owner_id, upload_id)
    if current is not None and current.status.value == "ready":
        logger.info("upload %s already committed; skipping", upload_id)
        return True

    # Re-resolve table names against the database, which only this stage can
    # see. Stage 1 de-duplicates against `taken_table_names`, but the deployed
    # processor runs OUTSIDE the VPC and has no database, so `lambda_handler`
    # cannot pass that set — every upload there believes the name space is
    # empty. Uploading `ventas.csv` twice therefore produced two datasets both
    # claiming `ventas`, silently, in production only.
    #
    # Cheap and idempotent: locally the names are already unique, so nothing
    # changes and the loop is a no-op.
    from app.services import datasets as datasets_store

    taken = datasets_store.existing_table_names(owner_id)
    for dataset in result.datasets:
        resolved = unique_table_name(dataset.table_name, taken)
        if resolved != dataset.table_name:
            logger.info(
                "table name %r already taken; using %r for upload %s",
                dataset.table_name,
                resolved,
                upload_id,
            )
            dataset.table_name = resolved

    # Tell the user they already have a dataset by this name, and let them act
    # on it. A warning rather than a refusal, deliberately: re-uploading a file
    # to refresh its data is a legitimate thing to do — `unique_table_name`
    # exists precisely to support it — and the confirmation screen this warning
    # appears on has an editable name field, so the fix is one keystroke away.
    # Blocking the upload outright would make refreshing a dataset impossible.
    warnings = list(result.warnings)
    existing = {d.name.strip().casefold() for d in datasets_store.list_datasets(owner_id)}
    duplicates = sorted(
        {d.name for d in result.datasets if d.name.strip().casefold() in existing}
    )
    for name in duplicates:
        warnings.append(
            f"Ya tenés un dataset llamado “{name}”. Este se guarda por separado — "
            "renombralo abajo si querés distinguirlos."
        )

    try:
        uploads_store.commit(owner_id, upload_id, result.datasets, warnings)
    except Exception as exc:  # noqa: BLE001 - boundary: see below
        # The last write in the pipeline, and until now the only step without a
        # boundary. Everything upstream records its failure; if this one raised,
        # the row stayed `processing` and NOTHING ever moved it again — the
        # client polled a status that could not change, which is the "stuck on
        # the ingest screen forever" symptom.
        #
        # `fail()` is a separate session, so it still lands after
        # `session_scope()` rolled the commit back.
        logger.exception("stage 2 could not commit upload %s", upload_id)
        uploads_store.fail(owner_id, upload_id, IngestError(str(exc)).message)
        return False
    return True


# ---------------------------------------------------------------------------
# Both stages, in one process — the local runner and the tests
# ---------------------------------------------------------------------------


def process_upload(owner_id: str, upload_id: str) -> None:
    """Stage 1 then stage 2, in this process. What `ThreadRunner` executes.

    The same two calls the deployed Lambdas make, in the same order — so a bug
    in the sequence shows up on a laptop rather than in CloudWatch.
    """
    from app.services import datasets as datasets_store
    from app.services import uploads as uploads_store

    upload = uploads_store.get_upload(owner_id, upload_id)
    if upload is None:
        logger.warning("ingestion job for a missing upload: %s", upload_id)
        return

    result = run_stage1(
        owner_id,
        upload_id,
        upload.filename,
        file_type=upload.fileType,
        taken_table_names=datasets_store.existing_table_names(owner_id),
        # Locally the control plane is reachable, so the stepper can be driven
        # from it directly instead of polling status.json.
        on_phase=lambda step: uploads_store.set_phase(owner_id, upload_id, step),
    )
    if result.error is None:
        uploads_store.set_detected(owner_id, upload_id, len(result.datasets))
    commit_stage2(owner_id, upload_id)


__all__ = [
    "PhaseSink",
    "Stage1Result",
    "commit_stage2",
    "human_size",
    "process_upload",
    "read_status",
    "run_stage1",
    "schema_for",
    "unique_table_name",
]
