import { useRef, useState } from 'react'
import { Check, PaperPlaneRight, Plus, ShieldWarning } from '@phosphor-icons/react'
import { Button, Icon, Spinner, VegaChart } from '../../components/ui'
import type { AssistantMessage, GuardMessage } from '../../types'
import * as api from '../../lib/api'
import styles from './dashboards.module.css'

export interface AskForChartProps {
  dashboardId: string
  folderId: string
  folderName: string
  onAdded: () => void
}

type Answer = AssistantMessage | GuardMessage

/**
 * Ask a question from inside a dashboard and put the resulting chart on it.
 *
 * Runs the **same** prepare/execute cycle as the chat — generated SQL, the
 * read-only guardrail, Module A, Module C — through `api.askIn`. There is no
 * shortcut endpoint: a question asked from a board is the same work and the
 * same cost as one asked from the chat, so it goes through the same pipeline
 * and lands in History like any other.
 *
 * It owns its own conversation over the board's folder, created on the first
 * question and reused after, so follow-ups stay in one thread and the chat
 * screen's own conversation is left alone.
 */
export function AskForChart({ dashboardId, folderId, folderName, onAdded }: AskForChartProps) {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  // The id of the answer already added, if any. Guards against a second click
  // adding the same chart twice — which, now that the backend also pins a
  // freshly-asked chart to the folder's saved pool (`_pin_for_folder`), would
  // leave two duplicate pins behind as well as two duplicate cards. Compared
  // against `answer.id` rather than a plain boolean so it clears itself the
  // moment a new question produces a new answer, with nothing to reset by hand.
  const [addedId, setAddedId] = useState<string | null>(null)
  const conversationId = useRef<string | null>(null)

  const ask = async () => {
    const text = question.trim()
    if (!text || busy) return
    setBusy(true)
    setError(null)
    setAnswer(null)
    try {
      if (!conversationId.current) {
        const conversation = await api.createConversation({
          type: 'folder',
          id: folderId,
          name: folderName,
          datasets: [],
        })
        conversationId.current = conversation.id
      }
      setAnswer(await api.askIn(conversationId.current, text))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo responder esa pregunta.')
    } finally {
      setBusy(false)
    }
  }

  const add = async () => {
    if (!answer || answer.role !== 'assistant' || adding) return
    setAdding(true)
    try {
      await api.addDashboardChart(dashboardId, { messageId: answer.id })
      setAddedId(answer.id)
      onAdded()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo agregar ese gráfico.')
    } finally {
      setAdding(false)
    }
  }

  const assistant = answer?.role === 'assistant' ? answer : null
  const chart = assistant?.result.chart
  const isAdded = assistant?.id === addedId

  return (
    <div className={styles.askPanel}>
      <p className={styles.askIntro}>
        Preguntá sobre {folderName}. El gráfico de la respuesta se puede agregar directo a este dashboard.
      </p>

      <div className={styles.askRow}>
        <input
          className="field"
          placeholder="p. ej. ventas por mes"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void ask()
          }}
          disabled={busy}
        />
        <Button variant="primary" onClick={() => void ask()} disabled={busy || !question.trim()}>
          {busy ? <Spinner /> : <Icon icon={PaperPlaneRight} size="sm" />}
          Preguntar
        </Button>
      </div>

      {error && (
        <p className="hint" role="status">
          {error}
        </p>
      )}

      {answer?.role === 'guard' && (
        <div className={styles.askResult} role="status">
          <p className={styles.askNarrative}>
            <Icon icon={ShieldWarning} size="sm" /> {answer.message}
          </p>
        </div>
      )}

      {assistant && (
        <div className={styles.askResult}>
          <p className={styles.askNarrative}>{assistant.result.naturalResponse}</p>

          {chart ? (
            <>
              <VegaChart
                spec={chart.spec}
                table={assistant.result.table}
                height={220}
                label={typeof chart.spec.title === 'string' ? chart.spec.title : 'Gráfico'}
              />
              <div className={styles.askActions}>
                {!isAdded && (
                  <p className="hint" style={{ margin: 0, marginRight: 'auto' }}>
                    También se guarda en los gráficos guardados de esta carpeta.
                  </p>
                )}
                <Button variant="primary" onClick={() => void add()} disabled={adding || isAdded}>
                  <Icon icon={isAdded ? Check : Plus} size="sm" />
                  {adding ? 'Agregando…' : isAdded ? 'Agregado' : 'Agregar al dashboard'}
                </Button>
              </div>
            </>
          ) : (
            <p className="hint">
              Esta respuesta no tiene un gráfico para agregar — probá pedir un desglose, una
              tendencia o una comparación.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
