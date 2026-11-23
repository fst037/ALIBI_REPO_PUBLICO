import type { HistoryGroup, SavedQuery } from '../../types'
import * as gen from './generated/library/library'
import { authInit, unwrap } from './http'

/** Conversation history grouped by recency (Historial screen). */
export function listHistory(): Promise<HistoryGroup[]> {
  return unwrap<HistoryGroup[]>(gen.listHistory(authInit()))
}

export function listSavedQueries(): Promise<SavedQuery[]> {
  return unwrap<SavedQuery[]>(gen.listSavedQueries(undefined, authInit()))
}

export function searchSavedQueries(query: string): Promise<SavedQuery[]> {
  const q = query.trim()
  return unwrap<SavedQuery[]>(gen.listSavedQueries(q ? { q } : undefined, authInit()))
}
