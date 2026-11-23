import type { ChatSource, Dataset, Folder } from '../../types'

/**
 * Constructor for the chat's active source.
 *
 * **A conversation runs over one folder, and nothing else.** `datasetSource`
 * used to live here too, building a `dataset` or `datasets` source out of a
 * hand-picked selection; both were removed along with the API's source types.
 * The scope a question is answered against is now exactly one folder the user
 * chose — Module A never sees a single isolated table, and never sees everything
 * the user owns.
 *
 * Asking about one file means putting it in a folder. That is a real extra step
 * and it buys the thing an ad-hoc selection could not do: the folder is
 * re-resolved on every read, so a file dropped into it later joins a
 * conversation that is already open, and one taken out stops being answerable.
 */

/**
 * A whole folder.
 *
 * `datasets` is carried only so the canvas can render the source pills without
 * a refetch — it is *not* what gets sent. The API receives the folder id and
 * resolves the contents itself on every read.
 */
export function folderSource(folder: Folder, datasets: Dataset[]): ChatSource {
  return { type: 'folder', id: folder.id, name: folder.name, datasets }
}

/** Header label: the folder's real name, or a count as a last resort. */
export function sourceLabel(source: ChatSource): string {
  return source.name || `${source.datasets.length} datasets`
}
