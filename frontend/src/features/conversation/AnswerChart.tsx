import { useState } from 'react'
import { ArrowsOut, PushPin, Sparkle } from '@phosphor-icons/react'
import { Icon, VegaChart } from '../../components/ui'
import type { ChartSpec, ResultTable } from '../../types'
import { cx } from '../../lib/cx'
import * as api from '../../lib/api'
import { FullscreenChart } from './FullscreenChart'
import styles from './conversation.module.css'

export interface AnswerChartProps {
  chart: ChartSpec
  table: ResultTable
  /** Folder/dataset the conversation runs over — where "Save" pins the chart. */
  datasetId: string
  /** Server id of the assistant message this chart belongs to. */
  messageId?: string
  /** Which finding of a multi-finding answer this chart draws; omitted, the lead. */
  findingIndex?: number
}

/**
 * The chart half of an answer (flujo 3.2 + 3.4): the picture, Save, fullscreen.
 *
 * **The chart type is not switchable.** Module C reads the question and the
 * data and picks the form that answers it; offering six chips next to that
 * invites the user to overrule a choice they have less information to make than
 * the model did, and every alternative they can reach is worse by construction.
 * The chip row also implied a promise the product could not keep — a
 * scatter plot of one column, a heatmap of one dimension — so the chips are
 * gone along with the client-side re-encode that fed them.
 *
 * What is left is the honest pair: one chart, or none at all.
 */
export function AnswerChart({ chart, table, datasetId, messageId, findingIndex }: AnswerChartProps) {
  const [fullscreen, setFullscreen] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedId, setSavedId] = useState<string | null>(null)

  const title = typeof chart.spec.title === 'string' ? chart.spec.title : 'Visualización'

  const toggleSave = async () => {
    if (saving || !messageId) return
    setSaving(true)
    try {
      if (saved && savedId) {
        await api.removeSavedChart(savedId)
        setSaved(false)
        setSavedId(null)
      } else {
        const pinned = await api.saveChart(datasetId, messageId, chart.chartType, findingIndex)
        setSaved(true)
        setSavedId(pinned.id)
      }
    } catch {
      // Pinning is reversible and non-destructive; a failed toggle leaves the
      // button where it was rather than interrupting the conversation.
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={styles.chartCard}>
      <div className={styles.chartHeader}>
        <div className={styles.chartTitle}>
          <h4>{title}</h4>
          <p>{table.rows.length} registros · Módulo C</p>
        </div>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => setFullscreen(true)}
          aria-label="Ver en pantalla completa"
        >
          <Icon icon={ArrowsOut} size="sm" />
        </button>
      </div>

      <div className={styles.chartHost}>
        <VegaChart spec={chart.spec} table={table} label={title} />
      </div>

      <div className={styles.chartControls}>
        <p className={styles.autoNote}>
          <Icon icon={Sparkle} size={12} />
          Gráfico elegido automáticamente para esta respuesta.
        </p>
        <button
          type="button"
          className={cx(styles.saveBtn, saved && styles.saved)}
          onClick={() => void toggleSave()}
          disabled={saving || !messageId}
          aria-pressed={saved}
        >
          <Icon icon={PushPin} size="sm" weight={saved ? 'fill' : 'regular'} />
          {saved ? 'Guardado' : 'Guardar'}
        </button>
      </div>

      {fullscreen && (
        <FullscreenChart chart={chart} table={table} onClose={() => setFullscreen(false)} />
      )}
    </div>
  )
}
