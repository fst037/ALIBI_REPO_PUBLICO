"""Pydantic models for the `library` tag of `contracts/chat.yaml`:
conversation history and saved queries.
"""

from pydantic import BaseModel, Field

from app.schemas.charts import ChartType


class SavedQuery(BaseModel):
    id: str
    question: str
    datasetId: str
    datasetName: str
    date: str
    chartType: ChartType


class SaveQueryRequest(BaseModel):
    question: str = Field(min_length=1)
    datasetId: str
    chartType: ChartType


class HistoryItem(BaseModel):
    id: str
    title: str
    source: str
    questionCount: int
    time: str
    chartType: ChartType
    favorite: bool


class HistoryGroup(BaseModel):
    date: str
    items: list[HistoryItem]
