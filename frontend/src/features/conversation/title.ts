/**
 * Display title for a conversation, shared by the sidebar's Recent list and the
 * History screen so the same thread never reads differently in the two places.
 *
 * The server names a conversation after its first answered question; one that
 * was opened but never asked anything keeps the "New query" placeholder, which
 * renders as a column of identical rows. Those fall back to the folder they run
 * over, so the list still tells the user which thread is which.
 */
const PLACEHOLDER = 'new query'

export function conversationTitle(title: string, sourceName?: string): string {
  const trimmed = title.trim()
  if (trimmed && trimmed.toLowerCase() !== PLACEHOLDER) return trimmed
  return sourceName ? `Sin título · ${sourceName}` : 'Conversación sin título'
}
