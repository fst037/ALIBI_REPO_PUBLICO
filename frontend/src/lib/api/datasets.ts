import type {
  CleaningPreview,
  ColumnType,
  Dataset,
  DatasetQuality,
  DatasetSchema,
  DatasetStatus,
  DatasetUpdatePreview,
  DatasetUpdateRequest,
  SchemaColumn,
  TableCleaning,
  TablePreview,
  UpdateTarget,
} from '../../types'
import * as gen from './generated/datasets/datasets'
import { ApiError, authInit, unwrap } from './http'

/** Column types the schema editor can assign (Ingest screen, 1.2). */
export const COLUMN_TYPES: ColumnType[] = [
  'Text',
  'Number',
  'Whole number',
  'Date',
  'Percentage',
  'Boolean',
]

/**
 * Display text for a `ColumnType` — separate from the wire value itself.
 * `Boolean` reads as jargon in the type picker; "Verdadero/Falso" says what a user
 * actually sees in the column. The API contract keeps sending/expecting
 * `"Boolean"` — only the label shown to the user changes.
 */
export const COLUMN_TYPE_LABELS: Record<ColumnType, string> = {
  Text: 'Texto',
  Number: 'Número',
  'Whole number': 'Número entero',
  Date: 'Fecha',
  Percentage: 'Porcentaje',
  Boolean: 'Verdadero/Falso',
}

/**
 * Datasets in the library, optionally narrowed.
 *
 * `folderId` is what lets a folder be picked as a single source (a chat or a
 * dashboard over a whole "project"); `status` is how the confirmation screen
 * finds the tables an upload just produced.
 */
export function listDatasets(filter?: { folderId?: string; status?: DatasetStatus }): Promise<Dataset[]> {
  return unwrap<Dataset[]>(gen.listDatasets(filter, authInit()))
}

export async function getDataset(id: string): Promise<Dataset | null> {
  try {
    return await unwrap<Dataset>(gen.getDataset(id, authInit()))
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

export async function getSchema(datasetId: string): Promise<DatasetSchema | null> {
  try {
    return await unwrap<DatasetSchema>(gen.getSchema(datasetId, authInit()))
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

/**
 * First rows of a stored table, read through DuckDB.
 *
 * Returns null on 409 — the dataset exists but its table isn't materialised
 * yet, which is an ordinary state during processing rather than an error worth
 * throwing at a drawer that is merely showing a peek.
 */
export async function previewDataset(datasetId: string, limit = 20): Promise<TablePreview | null> {
  try {
    return await unwrap<TablePreview>(gen.previewDataset(datasetId, { limit }, authInit()))
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 409)) return null
    throw err
  }
}

/**
 * Confirm the (possibly edited) schema and activate the dataset.
 *
 * `name` matters most for a multi-table upload, where every detected table
 * arrives named after the same file and the user is the one who can tell them
 * apart.
 */
export function confirmSchema(
  datasetId: string,
  columns: SchemaColumn[],
  name?: string,
  cleaning?: TableCleaning,
): Promise<Dataset> {
  return unwrap<Dataset>(gen.confirmSchema(datasetId, { columns, name, cleaning }, authInit()))
}

/**
 * ETLite: empty cells, stray whitespace and duplicates in the stored table.
 *
 * Null when the table isn't materialised (409) — the panel simply has nothing
 * to offer yet, which is not an error worth surfacing.
 */
export async function getDatasetQuality(datasetId: string): Promise<DatasetQuality | null> {
  try {
    return await unwrap<DatasetQuality>(gen.getDatasetQuality(datasetId, authInit()))
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 409)) return null
    throw err
  }
}

/**
 * The table as these (unsaved) edits would leave it. Throws `ApiError` 422
 * `invalid_cleaning` with a user-facing message when a step cannot apply — the
 * same check confirming runs, so what previews is what saves.
 */
export function previewCleaning(
  datasetId: string,
  columns: SchemaColumn[],
  cleaning?: TableCleaning,
  limit = 8,
): Promise<CleaningPreview> {
  return unwrap<CleaningPreview>(gen.previewCleaning(datasetId, { columns, cleaning }, { limit }, authInit()))
}

/** Update a dataset — rename and/or toggle favorite (Library). */
export function updateDataset(id: string, patch: { name?: string; favorite?: boolean }): Promise<Dataset> {
  return unwrap<Dataset>(gen.updateDataset(id, patch, authInit()))
}

/** Mark/unmark a dataset as favorite. */
export function setDatasetFavorite(id: string, favorite: boolean): Promise<Dataset> {
  return updateDataset(id, { favorite })
}

/** Rename a dataset. */
export function renameDataset(id: string, name: string): Promise<Dataset> {
  return updateDataset(id, { name })
}

/** Delete a dataset (irreversible — the UI confirms first). */
export async function deleteDataset(id: string): Promise<void> {
  await unwrap<void>(gen.deleteDataset(id, authInit()))
}

/**
 * Confirmed datasets the freshly uploaded table (`stagedDatasetId`) could
 * append to or replace, fitting ones first. Empty when there is nothing to offer.
 */
export function getUpdateTargets(stagedDatasetId: string): Promise<UpdateTarget[]> {
  return unwrap<UpdateTarget[]>(gen.getUpdateTargets(stagedDatasetId, authInit()))
}

/**
 * What appending to / replacing `datasetId` would do, nothing saved. A structure
 * difference is `compatible: false` with the list, not a thrown error.
 */
export function previewDatasetUpdate(
  datasetId: string,
  request: DatasetUpdateRequest,
): Promise<DatasetUpdatePreview> {
  return unwrap<DatasetUpdatePreview>(gen.previewDatasetUpdate(datasetId, request, authInit()))
}

/**
 * Apply it. The dataset keeps its id and column names, so charts and boards
 * built on it keep working. Throws `ApiError` 422 `structure_mismatch`,
 * `invalid_cleaning`, `incompatible_values` or `row_limit_exceeded`.
 */
export function applyDatasetUpdate(datasetId: string, request: DatasetUpdateRequest): Promise<Dataset> {
  return unwrap<Dataset>(gen.applyDatasetUpdate(datasetId, request, authInit()))
}
