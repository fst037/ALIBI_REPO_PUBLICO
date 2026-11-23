import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { ArrowsIn } from '@phosphor-icons/react'
import { Button, Eyebrow, Icon, VegaChart } from '../../components/ui'
import type { ChartSpec, ResultTable } from '../../types'
import { cx } from '../../lib/cx'
import styles from './conversation.module.css'

export interface FullscreenChartProps {
  chart: ChartSpec
  table: ResultTable
  onClose: () => void
}

/**
 * Fullscreen overlay for one answer's chart.
 *
 * Rendered via a portal to escape the answer card's backdrop-filter, which
 * would otherwise trap a position:fixed overlay inside the card.
 *
 * The type selector that used to sit under the chart is gone with the rest of
 * the switching UI — this shows the chart Module C chose, larger.
 */
export function FullscreenChart({ chart, table, onClose }: FullscreenChartProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const title = typeof chart.spec.title === 'string' ? chart.spec.title : 'Visualización'

  return createPortal(
    <div className={styles.fsScrim}>
      <div className={styles.fsHead}>
        <div>
          <Eyebrow>Visualización · Módulo C</Eyebrow>
          <h3 className={styles.fsTitle}>{title}</h3>
        </div>
        <Button variant="glass" onClick={onClose}>
          <Icon icon={ArrowsIn} size="sm" />Salir de pantalla completa
        </Button>
      </div>
      <div className={styles.fsBody}>
        <div className={styles.fsChartWrap}>
          <div className={cx('glass', styles.fsChart)}>
            <VegaChart spec={chart.spec} table={table} height={460} label={title} />
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
