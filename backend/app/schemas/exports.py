"""Pydantic models for the `exports` domain (`contracts/visualization.yaml`)."""

from enum import Enum
from typing import Union

from pydantic import BaseModel

from app.schemas.charts import ChartType


class ExportFormat(str, Enum):
    csv = "csv"
    png = "png"
    pdf = "pdf"


class ExportFromMessage(BaseModel):
    messageId: str


class ExportFromSavedChart(BaseModel):
    savedChartId: str


# Exactly one of messageId / savedChartId (distinct required fields).
ExportSource = Union[ExportFromMessage, ExportFromSavedChart]


class ExportRequest(BaseModel):
    source: ExportSource
    format: ExportFormat
    # Required for png/pdf, ignored for csv.
    chartType: ChartType | None = None


class ExportResult(BaseModel):
    downloadUrl: str
    format: ExportFormat
    expiresAt: str
