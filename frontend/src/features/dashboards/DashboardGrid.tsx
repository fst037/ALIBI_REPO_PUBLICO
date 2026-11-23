import { lazy, Suspense, useMemo } from 'react'
import { Skeleton } from '../../components/ui'
import type { ChartLayout, DashboardChart } from '../../types'
import { LiveChartCard } from './LiveChartCard'
import styles from './dashboards.module.css'

/**
 * `react-grid-layout` is ~35 kB and only this screen uses it, so it loads with
 * the screen rather than with the app — the same treatment `vega-embed` gets in
 * `VegaChart`, and for the same reason. `GridSurface` exists as a separate
 * module purely so the import can be lazy: it is the only thing that touches
 * the library.
 */
const GridSurface = lazy(async () => ({
  default: (await import('./GridSurface')).GridSurface,
}))

export interface DashboardGridProps {
  dashboardId: string
  charts: DashboardChart[]
  refreshToken: number
  onLayoutChange: (layout: Record<string, ChartLayout>) => void
  onRemove: (chartId: string) => void
}

export function DashboardGrid({
  dashboardId,
  charts,
  refreshToken,
  onLayoutChange,
  onRemove,
}: DashboardGridProps) {
  const items = useMemo(
    () => charts.map((c) => ({ id: c.id, ...c.layout })),
    [charts],
  )

  return (
    <Suspense fallback={<Skeleton variant="block" height={320} />}>
      <GridSurface items={items} onArrange={onLayoutChange}>
        {charts.map((chart) => (
          <div key={chart.id} className={styles.gridItem}>
            <LiveChartCard
              dashboardId={dashboardId}
              chart={chart}
              refreshToken={refreshToken}
              onRemove={() => onRemove(chart.id)}
            />
          </div>
        ))}
      </GridSurface>
    </Suspense>
  )
}
