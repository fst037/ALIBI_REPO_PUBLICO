import type {
  AssistantMessage,
  ChatSource,
  Conversation,
  GuardMessage,
  Suggestion,
} from '../../types'
import * as gen from './generated/conversations/conversations'
import type { Run, RunMode, RunStep } from './generated/model'
import { ApiError, authInit, unwrap } from './http'

export type { RunMode, RunStep }

/** Options for asking: the depth, and a callback that receives each new set of steps. */
export interface AskOptions {
  /** This question's depth. Omitted, the conversation's own default applies. */
  mode?: RunMode
  onProgress?: (steps: RunStep[]) => void
}

/** How often a running answer is polled, and for how long at most. */
const POLL_MS = 1000
// A little above the server's own staleness cut-off (960 s), which reports a run
// whose worker died as failed — the client never needs to give up first.
const POLL_LIMIT_MS = 17 * 60 * 1000

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

function genId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 9)}`
}

export function listConversations(): Promise<Conversation[]> {
  return unwrap<Conversation[]>(gen.listConversations(undefined, authInit()))
}

export async function getConversation(id: string): Promise<Conversation | null> {
  try {
    return await unwrap<Conversation>(gen.getConversation(id, authInit()))
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

export function getRecentConversations(limit = 6): Promise<Conversation[]> {
  return unwrap<Conversation[]>(gen.listConversations({ recent: true, limit }, authInit()))
}

/** Rename a conversation and/or star it (History). */
export function updateConversation(
  id: string,
  patch: { title?: string; favorite?: boolean; mode?: RunMode },
): Promise<Conversation> {
  return unwrap<Conversation>(gen.updateConversation(id, patch, authInit()))
}

/** Star/unstar a conversation. */
export function setConversationFavorite(id: string, favorite: boolean): Promise<Conversation> {
  return updateConversation(id, { favorite })
}

/**
 * Delete a conversation and its messages (irreversible — the UI confirms first).
 *
 * Also drops it from the seam's "current conversation" if that is the one being
 * deleted: the next question would otherwise be appended to a row the server no
 * longer has, and come back as a 404 the user cannot act on.
 */
export async function deleteConversation(id: string): Promise<void> {
  await unwrap<void>(gen.deleteConversation(id, authInit()))
  if (currentConversationId === id) resetConversation()
}

export function getSuggestions(datasetId?: string): Promise<Suggestion[]> {
  return unwrap<Suggestion[]>(gen.getSuggestions(datasetId ? { datasetId } : undefined, authInit()))
}

/**
 * Open a conversation over a folder.
 *
 * The folder id travels, never its expanded dataset ids: that is what makes the
 * API re-resolve the contents on every read, so a dataset added to the folder
 * later joins this conversation. Sending the ids would freeze the list at this
 * moment — and the API refuses them outright now, rather than guessing a scope
 * the caller did not choose.
 */
export function createConversation(source: ChatSource, mode?: RunMode): Promise<Conversation> {
  return unwrap<Conversation>(
    gen.createConversation({ folderId: source.id, mode }, authInit()),
  )
}

/*
 * The seam owns the "current" conversation for the chat screen, created lazily
 * over the active data source. When the source changes, a new conversation
 * is opened; `resetConversation` (called on "new query") clears it.
 */
let currentConversationId: string | null = null
let currentSourceKey: string | null = null
// The depth chosen before the conversation exists — it is opened with it.
let pendingMode: RunMode = 'quick'

/**
 * Identity of a source for the "is this still the same conversation?" check.
 *
 * Just the folder id: it is the whole source now, and its contents are resolved
 * server-side on every read — so a file added to the folder must NOT look like a
 * different source and open a second conversation.
 */
function sourceKey(source: ChatSource): string {
  return source.id
}

async function ensureConversation(source: ChatSource): Promise<string> {
  const key = sourceKey(source)
  if (currentConversationId && key === currentSourceKey) return currentConversationId
  if (!source.id) {
    // No folder chosen. There is no sensible default any more — falling back to
    // "the first dataset" is exactly the implicit scope this design removed.
    throw new Error('Elegí una carpeta antes de hacer una pregunta.')
  }
  const conversation = await createConversation(source, pendingMode)
  currentConversationId = conversation.id
  currentSourceKey = key
  return conversation.id
}

/**
 * Continue an existing conversation instead of opening a new one.
 *
 * Called when a thread is reopened from History or the sidebar. Without it the
 * next question would open a *second* conversation over the same folder, and
 * the replayed thread and the new answer would end up in different rows.
 */
export function adoptConversation(id: string, source: ChatSource): void {
  currentConversationId = id
  currentSourceKey = sourceKey(source)
}

/** Drop the current conversation so the next question opens a fresh one. */
export function resetConversation(): void {
  currentConversationId = null
  currentSourceKey = null
  pendingMode = 'quick'
}

/**
 * Set the chat's default depth: saved on the current conversation, or
 * remembered for the one the next question opens.
 *
 * Saving is best-effort. Every question carries its depth explicitly anyway, so
 * a lost PATCH changes only what a reopened conversation starts with — never
 * how a question is answered.
 */
export async function setAnswerMode(mode: RunMode): Promise<void> {
  pendingMode = mode
  if (!currentConversationId) return
  await updateConversation(currentConversationId, { mode }).catch(() => undefined)
}

/**
 * Ask a natural-language question in the active source's conversation.
 * Returns the assistant answer, or a guardrail rejection for write attempts.
 * `source` is the active data source; a change opens a new conversation.
 */
export async function sendQuestion(
  question: string,
  source: ChatSource = { type: 'datasets', id: '', name: '', datasets: [] },
  options: AskOptions = {},
): Promise<AssistantMessage | GuardMessage> {
  return askIn(await ensureConversation(source), question, options)
}

/**
 * Answer a question in a conversation the caller already has.
 *
 * Asynchronous on the server: this starts a
 * run, which returns at once, then polls it — reporting each new set of steps
 * through `onProgress` — until it ends. An answer can take longer than one
 * request may last (29 s), and the agent harness will routinely take longer.
 *
 * Split out of `sendQuestion` for the dashboard's in-situ chat, which must not
 * touch the module-level "current conversation" above. That pointer belongs to
 * the chat screen; a question asked from a dashboard that moved it would send
 * the user's *next* chat question into a conversation they never opened.
 *
 * Everything else is identical on purpose — the same run, guardrail and models.
 * A question asked from a dashboard is not a cheaper kind of question, so it
 * does not get a cheaper pipeline.
 */
export async function askIn(
  conversationId: string,
  question: string,
  { mode, onProgress }: AskOptions = {},
): Promise<AssistantMessage | GuardMessage> {
  let run = await unwrap<Run>(gen.startRun(conversationId, { question, mode }, authInit()))
  const deadline = Date.now() + POLL_LIMIT_MS
  let reported = -1
  while (run.status === 'queued' || run.status === 'running') {
    if (run.steps.length !== reported || run.steps.some((s) => s.status === 'running')) {
      reported = run.steps.length
      onProgress?.(run.steps)
    }
    if (Date.now() > deadline) {
      throw new ApiError(504, {
        error: { code: 'run_timeout', message: 'La respuesta tardó demasiado. Probá de nuevo.' },
      })
    }
    await sleep(POLL_MS)
    run = await unwrap<Run>(gen.getRun(conversationId, run.id, authInit()))
  }
  onProgress?.(run.steps)

  if (run.status === 'failed') {
    // The same code/message pair the synchronous endpoints answered with, so
    // every caller's existing error handling keeps working.
    throw new ApiError(409, { error: run.error ?? { code: 'unknown_error', message: '' } })
  }
  if (run.guard) {
    // Carry the server's reason through: the guardrail blocks more than write
    // attempts (SQL naming tables outside the dataset, multi-statement queries),
    // and showing the wrong explanation is worse than showing none.
    return {
      id: genId('m'),
      role: 'guard',
      question,
      at: new Date().toISOString(),
      message: run.guard.message,
    }
  }
  return run.message as AssistantMessage
}
