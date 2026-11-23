import { useRef, useState } from 'react'
import * as api from '../../lib/api'
import type { RunMode, RunStep } from '../../lib/api'
import { ApiError } from '../../lib/api/http'
import type { ChatSource, Message, QueryResult } from '../../types'

export type TurnPhase = 'piping' | 'answer' | 'guard'

export interface QaTurn {
  kind: 'qa'
  id: string
  question: string
  phase: TurnPhase
  /** The run's steps as the server reports them while the answer is built. */
  steps: RunStep[]
  result?: QueryResult
  /** Server message id of the answer — needed to pin its chart to a dashboard. */
  messageId?: string
  /** Why the guardrail blocked this turn (only set when phase === 'guard'). */
  guardMessage?: string
  /** The turn was refused by the plan (quota or overage cap), not the guardrail. */
  planLimit?: boolean
  /** The depth this question was asked at — shown when it differs from the default. */
  mode?: RunMode
}

export interface SystemNotice {
  kind: 'system'
  id: string
  text: string
}

export type ThreadItem = QaTurn | SystemNotice

function genId(): string {
  return Math.random().toString(36).slice(2, 9)
}

/**
 * "A conversation was created or renamed." The sidebar's Recent list listens for
 * it: answering a question is what names a conversation server-side, and without
 * a nudge the list would keep showing the state it had when it mounted.
 */
export const CONVERSATIONS_CHANGED = 'alibi:conversations-changed'

/** 402s from the plan: the fix is in Cuenta, not in the question. */
const PLAN_LIMIT_CODES = new Set(['plan_limit_reached', 'overage_cap_reached', 'plan_upgrade_required', 'deep_outside_quota'])

/**
 * Owns the conversation thread. Sending a question shows the server's real steps
 * as the answer run reports them (lib/api polls it), then the answer or the
 * guardrail rejection.
 */
export function useConversation() {
  const [messages, setMessages] = useState<ThreadItem[]>([])
  // Active data source for the thread — passed to the seam on each send. The
  // whole descriptor, not just ids: a folder has to reach the API as a folder.
  const source = useRef<ChatSource>({ type: 'datasets', id: '', name: '', datasets: [] })
  // Quick or Deep — the conversation's default, applied to every next
  // question until it is changed, saved on the conversation so reopening it
  // from History keeps it. A new conversation starts Quick.
  const [mode, setModeState] = useState<RunMode>('quick')

  const setMode = (next: RunMode) => {
    setModeState(next)
    void api.setAnswerMode(next)
  }

  const patchTurn = (id: string, patch: Partial<QaTurn>) => {
    setMessages((m) => m.map((it) => (it.kind === 'qa' && it.id === id ? { ...it, ...patch } : it)))
  }

  /**
   * Ask a question. `override` answers this one question at another depth and
   * leaves the conversation's default alone. The depth always travels with the
   * question, so an answer never depends on whether saving the default landed.
   */
  const send = async (question: string, override?: RunMode) => {
    const trimmed = question.trim()
    if (!trimmed) return
    const id = genId()
    const depth = override ?? mode
    setMessages((m) => [...m, { kind: 'qa', id, question: trimmed, phase: 'piping', steps: [], mode: depth }])

    // Steps from an earlier attempt of this same turn, kept above the new run's.
    let carried: RunStep[] = []
    for (let attempt = 0; ; attempt++) {
      try {
        const res = await api.sendQuestion(trimmed, source.current, {
          mode: depth,
          onProgress: (steps) => patchTurn(id, { steps: [...carried, ...steps] }),
        })
        if (res.role === 'guard') {
          patchTurn(id, { phase: 'guard', guardMessage: res.message })
        } else {
          patchTurn(id, { phase: 'answer', result: res.result, messageId: res.id })
          window.dispatchEvent(new Event(CONVERSATIONS_CHANGED))
        }
        return
      } catch (err) {
        // The fine-tuned models scale to zero when idle, and coming back can
        // outlast one run's wait. By then the
        // model is minutes from ready, so the question is asked again once, on
        // its own — making the user re-type it to get past a cold start would
        // be the product's problem handed to them.
        if (attempt === 0 && err instanceof ApiError && err.code === 'model_waking') {
          carried = [
            {
              id: `${id}-waking`,
              label: 'El modelo seguía iniciándose — vuelvo a preguntar',
              status: 'done',
              startedAt: new Date().toISOString(),
            },
          ]
          patchTurn(id, { steps: carried })
          continue
        }
        // Without this the turn sits on the pipeline animation forever: the
        // rejection was unhandled, so nothing ever moved it off `piping`. Real
        // failures reach here — an unconfirmed dataset (409), SQL that failed at
        // execution, an unreachable model — and the server's message is already
        // user-facing copy, so it is shown rather than replaced.
        patchTurn(id, {
          phase: 'guard',
          guardMessage:
            err instanceof ApiError ? err.message : 'No pudimos ejecutar esa consulta. Probá de nuevo.',
          planLimit: err instanceof ApiError && PLAN_LIMIT_CODES.has(err.code),
        })
        return
      }
    }
  }

  const addSystemNotice = (text: string) => {
    setMessages((m) => [...m, { kind: 'system', id: genId(), text }])
  }

  /** Set the active data source for subsequent questions. */
  const setSource = (next: ChatSource) => {
    source.current = next
  }

  const reset = () => {
    setMessages([])
    setModeState('quick')
    api.resetConversation()
  }

  /**
   * Replay a stored conversation into the thread.
   *
   * User messages are skipped on purpose: an assistant message already carries
   * the `question` it answered, so rendering both would double every question.
   * A stored turn is never `piping` — it finished before it was ever stored.
   */
  const load = (stored: Message[], storedMode: RunMode = 'quick') => {
    setModeState(storedMode)
    const items: ThreadItem[] = []
    for (const m of stored) {
      if (m.role === 'assistant') {
        items.push({
          kind: 'qa',
          id: m.id,
          question: m.question,
          phase: 'answer',
          steps: [],
          result: m.result,
          messageId: m.id,
        })
      } else if (m.role === 'guard') {
        items.push({ kind: 'qa', id: m.id, question: m.question, phase: 'guard', steps: [], guardMessage: m.message })
      } else if (m.role === 'system') {
        items.push({ kind: 'system', id: m.id, text: m.text })
      }
    }
    setMessages(items)
  }

  return { messages, send, addSystemNotice, reset, load, setSource, mode, setMode }
}
