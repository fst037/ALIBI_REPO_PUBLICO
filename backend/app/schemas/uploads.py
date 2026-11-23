"""Pydantic models for the `uploads` domain (Module B ingestion).

Conforms to `contracts/ingestion.yaml` v0.2. The upload flow replaced the v0.1
`POST /datasets` multipart endpoint: the API never receives the file bytes, it
hands out a presigned URL and the browser PUTs straight to the object store —
so upload size isn't capped by API Gateway's payload limit, and the API Lambda
isn't tied up for the duration of a large transfer.

One upload can yield SEVERAL datasets (a spreadsheet with three tables in one
sheet, a PDF with tables on different pages). That's the reason this is its own
entity rather than a field on `Dataset`.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.datasets import Dataset, DatasetFileType


class UploadStatus(str, Enum):
    # URL issued, bytes not received yet.
    pending = "pending"
    # The object landed; the worker hasn't picked it up.
    uploaded = "uploaded"
    processing = "processing"
    # Tables detected and materialised; each is a Dataset awaiting confirmation.
    ready = "ready"
    error = "error"


class UploadPhase(str, Enum):
    """Sub-step within `processing`; drives the stepper in Ingesta 1.1."""

    uploading = "uploading"
    extracting = "extracting"
    profiling = "profiling"
    inferring = "inferring"
    storing = "storing"
    done = "done"


class Upload(BaseModel):
    id: str
    filename: str
    fileType: DatasetFileType
    sizeBytes: int
    status: UploadStatus
    createdAt: datetime
    sizeLabel: str | None = None
    phase: UploadPhase | None = None
    detectedTables: int | None = None
    datasets: list[Dataset] = Field(default_factory=list)
    # Non-fatal things the user should know before confirming: "we found 3
    # tables", "a totals row was discarded", "we generated the column names".
    # The pipeline's guesses are visible rather than silent.
    warnings: list[str] = Field(default_factory=list)
    # Plain-language failure message — no stack traces, no library names.
    error: str | None = None
    updatedAt: datetime | None = None


class CreateUploadRequest(BaseModel):
    filename: str = Field(min_length=1)
    sizeBytes: int = Field(gt=0)
    contentType: str | None = None
    # When set, the resulting datasets are attached to this folder on confirm.
    folderId: str | None = None


class CreateUploadResponse(BaseModel):
    upload: Upload
    uploadUrl: str
    method: str = "PUT"
    headers: dict[str, str] = Field(default_factory=dict)
    expiresAt: datetime
