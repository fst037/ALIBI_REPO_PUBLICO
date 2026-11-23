import type { Folder, FolderDetail } from '../../types'
import * as gen from './generated/folders/folders'
import { ApiError, authInit, unwrap } from './http'

/* ============================================================
   Folders — one-level "projects" that group datasets.

   The relation to a dataset is a **mapping**, never ownership: `attach`
   creates a link and `detachDataset` removes one, while the dataset itself is
   untouched either way. `deleteFolder` likewise deletes the folder and its
   links, never the datasets in it. That is the product rule, and these names
   are chosen so the calling code reads as the rule rather than against it —
   there is deliberately no `moveDataset`.
   ============================================================ */

export function listFolders(): Promise<Folder[]> {
  return unwrap<Folder[]>(gen.listFolders(authInit()))
}

export async function getFolder(id: string): Promise<FolderDetail | null> {
  try {
    return await unwrap<FolderDetail>(gen.getFolder(id, authInit()))
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

/** Create a folder, optionally attaching datasets straight away.
 *  Throws `ApiError` with code `folder_name_taken` (409) on a duplicate name. */
export function createFolder(name: string, datasetIds: string[] = []): Promise<Folder> {
  return unwrap<Folder>(gen.createFolder({ name, datasetIds }, authInit()))
}

export function renameFolder(id: string, name: string): Promise<Folder> {
  return unwrap<Folder>(gen.updateFolder(id, { name }, authInit()))
}

/** Deletes the folder and its links only — every dataset stays in the library. */
export async function deleteFolder(id: string): Promise<void> {
  await unwrap<void>(gen.deleteFolder(id, authInit()))
}

/** Link existing datasets into a folder. Idempotent, and never copies data. */
export function attachDatasets(id: string, datasetIds: string[]): Promise<FolderDetail> {
  return unwrap<FolderDetail>(gen.attachDatasets(id, { datasetIds }, authInit()))
}

/** Remove the link. The dataset is not deleted. */
export async function detachDataset(folderId: string, datasetId: string): Promise<void> {
  await unwrap<void>(gen.detachDataset(folderId, datasetId, authInit()))
}
