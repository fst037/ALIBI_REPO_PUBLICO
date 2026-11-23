import { useEffect, useState } from 'react'
import { Check } from '@phosphor-icons/react'
import { Icon, Spinner } from '../../components/ui'
import type { RunStep } from '../../lib/api'
import { ToolCall } from './ToolCall'
import { cx } from '../../lib/cx'
import styles from './conversation.module.css'

/** A running step shows how long it has been running once it passes this. */
const SHOW_ELAPSED_AFTER_MS = 10_000

function elapsed(startedAt: string | undefined, now: number): string | null {
  const started = startedAt ? Date.parse(startedAt) : NaN
  if (Number.isNaN(started)) return null
  const ms = Math.max(0, now - started)
  if (ms < SHOW_ELAPSED_AFTER_MS) return null
  const total = Math.floor(ms / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

/**
 * What the answer is doing right now, step by step, as the server's run reports
 * it. Before the first step lands there is one
 * honest placeholder — the run is queued — not an invented sequence.
 *
 * Each step carries the planner's own one-line reason (`thought`) and, right
 * under it, the call that reason led to — Module A's query and its rows, a look
 * at a result, Module C's chart — so the user sees what was tried, by which
 * model, and what came back, while it happens (`ToolCall`).
 *
 * A step that runs long shows its elapsed time. Some legitimately take minutes —
 * waking the fine-tuned models after they scaled to zero is the longest
 * — and a spinner alone cannot tell the user
 * that work is still happening rather than stuck.
 */
export function PipelineSteps({ steps }: { steps: RunStep[] }) {
  const shown: RunStep[] = steps.length
    ? steps
    : [{ id: 'queued', label: 'Leyendo tu pregunta', status: 'running', startedAt: '' }]
  const running = shown.some((s) => s.status === 'running')
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])

  return (
    <div className={cx('glass', styles.ansCard)}>
      <div className={styles.pipe}>
        {shown.map((s) => {
          const done = s.status === 'done'
          const active = s.status === 'running'
          const time = active ? elapsed(s.startedAt, now) : null
          return (
            <div key={s.id} className={cx(styles.pipeStep, done && styles.done, active && styles.active)}>
              <span className={styles.pipeIco}>
                {done ? <Icon icon={Check} size="sm" /> : active ? <Spinner /> : null}
              </span>
              <span className={styles.pipeText}>
                {s.label}
                {s.thought && <span className={styles.pipeThought}>{s.thought}</span>}
                <ToolCall step={s} sqlInline />
              </span>
              {time && <span className={styles.pipeElapsed}>{time}</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
