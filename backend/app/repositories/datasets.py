"""Postgres repository for the `datasets` domain.

Each function takes an open `Session`; the service layer owns `session_scope()`.

Every query is scoped to `owner_id`: a dataset that isn't the caller's reads as
absent (None / no rows affected), so callers surface 404 rather than leaking or
touching another user's data.

Since Module B a dataset also carries its place in the *data* plane — the
Parquet key, the SQL table name it is exposed under, and which folders it is
linked into. The rows themselves are never here.
"""

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.db.models import (
    DatasetColumn,
    DatasetColumnEmbedding,
    DatasetModel,
    DatasetSchemaModel,
    FolderDataset,
)
from app.ingest.models import NewDataset
from app.schemas.datasets import (
    ConfirmSchemaRequest,
    Dataset,
    DatasetSchema,
    DatasetStatus,
    SchemaColumn,
    UpdateDatasetRequest,
)


def folder_map(session: Session, dataset_ids: list[str]) -> dict[str, list[str]]:
    """dataset_id → folder ids, in one query rather than one per dataset."""
    if not dataset_ids:
        return {}
    rows = session.exec(
        select(FolderDataset).where(FolderDataset.dataset_id.in_(dataset_ids))
    ).all()
    mapping: dict[str, list[str]] = {}
    for row in rows:
        mapping.setdefault(row.dataset_id, []).append(row.folder_id)
    return mapping


def to_dataset(row: DatasetModel, folder_ids: list[str] | None = None) -> Dataset:
    return Dataset(
        id=row.id,
        name=row.name,
        fileType=row.file_type,
        meta=row.meta or "",
        favorite=row.favorite,
        recent=row.recent,
        status=row.status,
        createdAt=row.created_at.date().isoformat(),
        updatedAt=row.updated_at.date().isoformat(),
        tableName=row.table_name,
        uploadId=row.upload_id,
        sourceLabel=row.source_label,
        folderIds=folder_ids or [],
        rows=row.row_count,
        pages=row.page_count,
        columns=row.column_count,
        sizeLabel=row.size_label,
        error=row.error,
    )


def list_datasets(
    session: Session,
    owner_id: str,
    *,
    folder_id: str | None = None,
    status: DatasetStatus | None = None,
) -> list[Dataset]:
    statement = select(DatasetModel).where(DatasetModel.owner_id == owner_id)
    if folder_id is not None:
        statement = statement.join(
            FolderDataset, FolderDataset.dataset_id == DatasetModel.id
        ).where(FolderDataset.folder_id == folder_id)
    if status is not None:
        statement = statement.where(DatasetModel.status == status)
    rows = session.exec(statement.order_by(DatasetModel.updated_at.desc())).all()
    folders = folder_map(session, [r.id for r in rows])
    return [to_dataset(row, folders.get(row.id)) for row in rows]


def get_dataset(session: Session, owner_id: str, dataset_id: str) -> Dataset | None:
    row = session.exec(
        select(DatasetModel).where(DatasetModel.id == dataset_id, DatasetModel.owner_id == owner_id)
    ).first()
    if row is None:
        return None
    return to_dataset(row, folder_map(session, [dataset_id]).get(dataset_id))


def _columns(session: Session, dataset_id: str) -> list[DatasetColumn]:
    return list(
        session.exec(
            select(DatasetColumn)
            .where(DatasetColumn.dataset_id == dataset_id)
            .order_by(DatasetColumn.ordinal)
        ).all()
    )


def get_schema(session: Session, owner_id: str, dataset_id: str) -> DatasetSchema | None:
    # Join datasets so the schema is only returned for the owner's dataset.
    head = session.exec(
        select(DatasetSchemaModel)
        .join(DatasetModel, DatasetModel.id == DatasetSchemaModel.dataset_id)
        .where(DatasetSchemaModel.dataset_id == dataset_id, DatasetModel.owner_id == owner_id)
    ).first()
    if head is None:
        return None
    columns = [
        SchemaColumn(
            name=col.name,
            type=col.type,
            sampleValues=col.sample_values or "",
            included=col.included,
            confidence=col.confidence,
            sourceName=col.source_name if col.source_name != col.name else None,
            vlType=col.vl_type,
            description=col.description,
            isPrimaryKey=col.is_primary_key,
        )
        for col in _columns(session, dataset_id)
    ]
    return DatasetSchema(
        datasetId=dataset_id,
        rowCount=head.row_count,
        confidence=head.confidence,
        columns=columns,
        tableDescription=head.table_description,
        confirmed=head.confirmed,
    )


def existing_table_names(session: Session, owner_id: str) -> set[str]:
    rows = session.exec(
        select(DatasetModel.table_name).where(
            DatasetModel.owner_id == owner_id, DatasetModel.table_name.is_not(None)
        )
    ).all()
    return {name for name in rows if name}


def projection(session: Session, owner_id: str, dataset_id: str) -> dict[str, str]:
    """Effective column name → its name inside the Parquet file.

    Empty when nothing has been renamed and nothing excluded, in which case the
    query layer reads the file as-is.
    """
    owned = session.exec(
        select(DatasetModel.id).where(
            DatasetModel.id == dataset_id, DatasetModel.owner_id == owner_id
        )
    ).first()
    if owned is None:
        return {}
    mapping: dict[str, str] = {}
    for col in _columns(session, dataset_id):
        physical = col.physical_name or col.name
        if not col.included:
            # An excluded column is left out of the view entirely, so a
            # generated query cannot reach a column the user hid.
            continue
        mapping[col.name] = physical
    return mapping


def physical_names(session: Session, owner_id: str, dataset_id: str) -> list[str]:
    """Every column's name inside the Parquet file, by ordinal — excluded ones too.

    ETLite addresses columns by ordinal (a rename must not move a recipe), so it
    needs the whole list, not the included-only `projection`.
    """
    owned = session.exec(
        select(DatasetModel.id).where(
            DatasetModel.id == dataset_id, DatasetModel.owner_id == owner_id
        )
    ).first()
    if owned is None:
        return []
    return [col.physical_name or col.name for col in _columns(session, dataset_id)]


def storage_key(session: Session, owner_id: str, dataset_id: str) -> str | None:
    return session.exec(
        select(DatasetModel.storage_key).where(
            DatasetModel.id == dataset_id, DatasetModel.owner_id == owner_id
        )
    ).first()


def update_dataset(session: Session, owner_id: str, dataset_id: str, request: UpdateDatasetRequest) -> Dataset | None:
    row = session.exec(
        select(DatasetModel).where(DatasetModel.id == dataset_id, DatasetModel.owner_id == owner_id)
    ).first()
    if row is None:
        return None
    # The request validator guarantees at least one field is set.
    if request.name is not None:
        row.name = request.name
    if request.favorite is not None:
        row.favorite = request.favorite
    session.add(row)
    session.flush()
    return to_dataset(row, folder_map(session, [dataset_id]).get(dataset_id))


def delete_dataset(session: Session, owner_id: str, dataset_id: str) -> bool:
    row = session.exec(
        select(DatasetModel).where(DatasetModel.id == dataset_id, DatasetModel.owner_id == owner_id)
    ).first()
    if row is None:
        return False
    # Folder links are rows about this dataset, so they go with it — deleting a
    # dataset is the one direction where the mapping does cascade.
    for link in session.exec(
        select(FolderDataset).where(FolderDataset.dataset_id == dataset_id)
    ).all():
        session.delete(link)
    for embedding in session.exec(
        select(DatasetColumnEmbedding).where(DatasetColumnEmbedding.dataset_id == dataset_id)
    ).all():
        session.delete(embedding)
    # dataset_schemas / dataset_columns rows cascade via their FKs.
    session.delete(row)
    return True


def insert_dataset(
    session: Session, owner_id: str, upload_id: str, new: NewDataset, folder_id: str | None
) -> None:
    """Persist one materialised table. Called by the ingestion commit."""
    session.add(
        DatasetModel(
            id=new.id,
            owner_id=owner_id,
            name=new.name,
            file_type=new.file_type,
            meta=new.meta,
            status=DatasetStatus.awaiting_confirmation,
            recent=True,
            row_count=new.row_count,
            column_count=new.column_count,
            size_label=new.size_label,
            storage_key=new.storage_key,
            table_name=new.table_name,
            upload_id=upload_id,
            source_label=new.source_label,
        )
    )
    # Flushed here, on its own: everything below references `new.id` via a
    # plain FK column, not an ORM `relationship()`, so the unit of work has no
    # dependency graph to order the inserts by and may batch the children
    # ahead of the parent. Without this, Postgres rejects the batch with a
    # "not present in table datasets" — never the parent's fault, always a
    # child inserted before its own row existed.
    session.flush()
    session.add(
        DatasetSchemaModel(
            dataset_id=new.id,
            row_count=new.schema.rowCount,
            confidence=new.schema.confidence,
            confirmed=False,
            table_description=new.schema.tableDescription,
        )
    )
    for ordinal, column in enumerate(new.schema.columns):
        session.add(
            DatasetColumn(
                dataset_id=new.id,
                ordinal=ordinal,
                name=column.name,
                source_name=column.sourceName or column.name,
                # At ingestion the effective name *is* the stored name; every
                # later rename is measured against this.
                physical_name=column.name,
                type=column.type,
                vl_type=column.vlType,
                description=column.description,
                is_primary_key=column.isPrimaryKey,
                sample_values=column.sampleValues,
                included=column.included,
                confidence=column.confidence,
            )
        )
    for ordinal, vector in enumerate(new.embeddings):
        session.add(
            DatasetColumnEmbedding(
                dataset_id=new.id,
                column_ordinal=ordinal,
                embedding=vector,
                model_id=new.embedding_model or "unknown",
            )
        )
    if folder_id is not None:
        session.add(FolderDataset(folder_id=folder_id, dataset_id=new.id))
    session.flush()


def confirm_schema(
    session: Session,
    owner_id: str,
    dataset_id: str,
    request: ConfirmSchemaRequest,
    *,
    row_count: int | None = None,
    size_label: str | None = None,
    meta: str | None = None,
    recipe: dict | None = None,
) -> Dataset | None:
    """`row_count`/`size_label`/`meta`, when given, describe the file ETLite rewrote."""
    dataset_row = session.exec(
        select(DatasetModel).where(DatasetModel.id == dataset_id, DatasetModel.owner_id == owner_id)
    ).first()
    if dataset_row is None:
        return None

    # The physical names must survive the replacement below — they describe the
    # Parquet file, which confirming does not touch. Correlated by ordinal,
    # which is what the client's list order means.
    previous = _columns(session, dataset_id)
    physical_by_ordinal = {c.ordinal: (c.physical_name or c.name) for c in previous}

    for col in previous:
        session.delete(col)
    session.flush()

    for ordinal, col in enumerate(request.columns):
        session.add(
            DatasetColumn(
                dataset_id=dataset_id,
                ordinal=ordinal,
                name=col.name,
                source_name=col.sourceName or col.name,
                physical_name=physical_by_ordinal.get(ordinal, col.name),
                type=col.type,
                vl_type=col.vlType,
                description=col.description,
                is_primary_key=col.isPrimaryKey,
                sample_values=col.sampleValues,
                included=col.included,
                confidence=col.confidence,
            )
        )

    # Mark the schema confirmed (if one exists) and activate the dataset.
    schema_row = session.get(DatasetSchemaModel, dataset_id)
    if schema_row is not None:
        schema_row.confirmed = True
        if row_count is not None:
            schema_row.row_count = row_count
        session.add(schema_row)

    if row_count is not None:
        dataset_row.row_count = row_count
    if size_label is not None:
        dataset_row.size_label = size_label
    if meta is not None:
        dataset_row.meta = meta

    if request.name is not None:
        dataset_row.name = request.name
    if recipe is not None:
        dataset_row.cleaning_recipe = recipe
    dataset_row.status = DatasetStatus.processed
    dataset_row.column_count = sum(1 for c in request.columns if c.included)
    dataset_row.confirmed_at = datetime.now(timezone.utc)
    session.add(dataset_row)
    session.flush()
    return to_dataset(dataset_row, folder_map(session, [dataset_id]).get(dataset_id))


def cleaning_recipe(session: Session, owner_id: str, dataset_id: str) -> dict | None:
    return session.exec(
        select(DatasetModel.cleaning_recipe).where(
            DatasetModel.id == dataset_id, DatasetModel.owner_id == owner_id
        )
    ).first()


def apply_update(
    session: Session,
    owner_id: str,
    dataset_id: str,
    staged_id: str,
    *,
    row_count: int,
    size_label: str,
    meta: str,
    samples: dict[int, str],
    recipe: dict | None,
) -> Dataset | None:
    """Record that the dataset's Parquet now holds the updated table, and drop the staged one.

    The dataset keeps its id, its columns' names and its folders — that is the
    whole point: charts and boards address it by id and by column name.
    """
    row = session.exec(
        select(DatasetModel).where(DatasetModel.id == dataset_id, DatasetModel.owner_id == owner_id)
    ).first()
    if row is None:
        return None
    row.row_count = row_count
    row.size_label = size_label
    row.meta = meta
    row.cleaning_recipe = recipe
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    schema_row = session.get(DatasetSchemaModel, dataset_id)
    if schema_row is not None:
        schema_row.row_count = row_count
        session.add(schema_row)
    if samples:
        for col in _columns(session, dataset_id):
            if col.ordinal in samples:
                col.sample_values = samples[col.ordinal]
                session.add(col)
    session.flush()
    delete_dataset(session, owner_id, staged_id)
    session.flush()
    return to_dataset(row, folder_map(session, [dataset_id]).get(dataset_id))
