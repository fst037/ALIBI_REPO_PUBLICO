import { useCallback, useEffect, useState } from 'react'
import { ArrowClockwise, ChatTeardropText, Warning, X } from '@phosphor-icons/react'
import { Icon, Skeleton, VegaChart } from '../../components/ui'
import type { DashboardChart, DashboardChartData } from '../../types'
import * as api from '../../lib/api'
import { cx } from '../../lib/cx'
import styles from './dashboards.module.css'

export interface LiveChartCardProps {
  dashboardId: string
  chart: DashboardChart
  /** Bumping this refetches — the board's "Refresh" button. */
  refreshToken?: number
  onRemove: () => void
}

/**
 * One card on a board: the chart's stored spec, drawn over data fetched now.
 *
 * The card owns its own request. That is the whole reason a board recomputes
 * per chart rather than in one call — a query that has gone stale (a renamed
 * column, a file taken out of the folder) marks this card and leaves every
 * other one alone, and no single request has to fit N queries inside API
 * Gateway's 29s.
 *
 * **No header row.** The chart is the whole card; the chat toggle and remove
 * button float over its top-left corner instead of claiming a permanent line
 * of their own. That also makes the chart itself the drag surface — there is
 * no `.liveHead` any more for react-grid-layout's `draggableHandle` to target,
 * so `GridSurface` cancels dragging only from the two floating buttons and
 * the question popover (`dragConfig.cancel`); a plain click there still
 * reaches its `onClick` because react-draggable only starts a drag past a few
 * pixels of movement.
 *
 * No title text anywhere on the card, either. Module C's own title is already
 * drawn inside the chart (`alibiVegaConfig.title`, from the spec Module C
 * wrote) — repeating it would say the same thing twice. What the chart can't
 * show is the question that produced it, so that's what the chat toggle
 * reveals: a small popover anchored under the button, positioned so it never
 * resizes the card or the chart inside it.
 */
export function LiveChartCard({ dashboardId, chart, refreshToken = 0, onRemove }: LiveChartCardProps) {
  const [data, setData] = useState<DashboardChartData | null>(null)
  const [loading, setLoading] = useState(true)
  const [showQuestion, setShowQuestion] = useState(false)

  const load = useCallback(() => {
    let alive = true
    setLoading(true)
    void api
      .getDashboardChartData(dashboardId, chart.id)
      .then((result) => {
        if (alive) setData(result)
      })
      .catch(() => {
        if (alive) {
          setData({
            chartId: chart.id,
            status: 'failed',
            rowCount: 0,
            message: 'No se pudo cargar este gráfico.',
          })
        }
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [dashboardId, chart.id])

  useEffect(() => load(), [load, refreshToken])

  return (
    <div className={styles.liveCard} aria-label={chart.title}>
      <div className={styles.liveFloatBtns}>
        {chart.question && (
          <button
            type="button"
            className={cx(styles.liveHeadBtn, showQuestion && styles.active)}
            onClick={() => setShowQuestion((v) => !v)}
            // Cancels the drag too (see `GridSurface`'s `dragConfig.cancel`);
            // this is the defence-in-depth layer for browsers/paths where that
            // selector-based check doesn't catch it.
            onPointerDown={(e) => e.stopPropagation()}
            aria-pressed={showQuestion}
            aria-label={showQuestion ? 'Ocultar la pregunta' : 'Mostrar la pregunta'}
            title={showQuestion ? 'Ocultar la pregunta' : 'Mostrar la pregunta'}
          >
            <Icon icon={ChatTeardropText} size="sm" />
          </button>
        )}
        <button
          type="button"
          className={styles.liveHeadBtn}
          onClick={onRemove}
          onPointerDown={(e) => e.stopPropagation()}
          aria-label={`Quitar ${chart.title} de este dashboard`}
        >
          <Icon icon={X} size="sm" />
        </button>
      </div>

      {showQuestion && chart.question && (
        <div className={styles.liveQ} onPointerDown={(e) => e.stopPropagation()}>
          <Icon icon={ChatTeardropText} size={12} />
          <span>{chart.question}</span>
        </div>
      )}

      <div className={styles.liveBody}>
        {loading && <Skeleton variant="block" height="100%" />}

        {!loading && data?.status === 'failed' && (
          <div className={styles.liveFail} role="status">
            <Icon icon={Warning} size="sm" />
            <p>{data.message ?? 'No se pudo reconstruir este gráfico con los datos actuales.'}</p>
            <button type="button" className="btn btn-glass btn-sm" onClick={() => load()}>
              <Icon icon={ArrowClockwise} size="sm" />Reintentar
            </button>
          </div>
        )}

        {!loading && data?.status === 'ok' && data.table && (
          <VegaChart
            spec={chart.spec}
            table={data.table}
            height={0}
            fill
            label={chart.title}
          />
        )}
      </div>
    </div>
  )
}
