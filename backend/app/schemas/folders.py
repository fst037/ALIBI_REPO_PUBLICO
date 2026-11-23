"""Pydantic models for the `folders` domain (Module B).

Conforms to `contracts/ingestion.yaml` v0.2.

Folders are one level deep — there is no `parentId` here and no `parent_id` in
the table, so "no subfolders" is a property of the schema rather than a rule
someone has to remember to enforce. The folder↔dataset relation is
many-to-many and is *only a mapping*: attaching a dataset to a second folder
creates a row in `folder_datasets`, never a copy of the data, and deleting a
folder deletes links, never datasets.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.datasets import Dataset


class Folder(BaseModel):
    id: str
    name: str
    datasetCount: int
    createdAt: datetime
    updatedAt: datetime | None = None


class FolderDetail(Folder):
    datasets: list[Dataset] = Field(default_factory=list)


class CreateFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    # Optional datasets to attach at creation time.
    datasetIds: list[str] = Field(default_factory=list)


class UpdateFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class AttachDatasetsRequest(BaseModel):
    datasetIds: list[str] = Field(min_length=1)
