import { useEffect, useMemo, useState } from 'react'
import { MagnifyingGlass, PushPin, X } from '@phosphor-icons/react'
import { CHART_TYPES, EmptyState, Icon, Segmented, Skeleton } from '../../components/ui'
import type { ChartType, SavedChart } from '../../types'
import * as api from '../../lib/api'
import { AskForChart } from './AskForChart'
import { ChartPreview, specHasColorChannel } from './ChartPreview'
import styles from './dashboards.module.css'

export interface AddChartPanelProps {
  dashboardId: string
  folderId: string
  folderName: string
  onClose: () => void
  /** A chart was added — the board reloads. */
  onAdded: () => void
}

type Tab = 'saved' | 'ask'

/** The chart's own title if Module C wrote one, else the question that made it. */
function chartName(chart: SavedChart): string {
  const title = chart.result.chart?.spec.title
  return typeof title === 'string' && title.trim() ? title.trim() : chart.question
}

/**
 * The two ways a chart gets onto a board (flujo 4.4), docked to the right of
 * the board rather than floating over it — open it and the grid reflows to
 * make room, the way VSCode's Copilot panel shares the window with the editor
 * instead of covering it.
 *
 * **Saved** lists charts pinned from conversations over this board's folder.
 * The server only offers ones it can rebuild — a chart pinned before Module C
 * is a picture rather than a query, so it could never refresh. Each row shows
 * a decorative preview (shape + colour count, not the chart's real values —
 * see `ChartPreview`), and can be filtered by name and by type.
 *
 * **Ask** runs a question here, through the ordinary prepare/execute cycle:
 * generated SQL, the read-only guardrail, and the cost estimate the product
 * treats as non-skippable (flujo 4.3.1). It is the same pipeline as the chat
 * because a question asked from a dashboard is not a cheaper kind of question.
 */
export function AddChartPanel({ dashboardId, folderId, folderName, onClose, onAdded }: AddChartPanelProps) {
  const [tab, setTab] = useState<Tab>('saved')
  const [saved, setSaved] = useState<SavedChart[]>([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState<ChartType | 'all'>('all')

  useEffect(() => {
    let alive = true
    setLoading(true)
    void api
      .listAvailableCharts(dashboardId)
      .then((charts) => alive && setSaved(charts))
      .catch(() => alive && setSaved([]))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [dashboardId])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return saved.filter((chart) => {
      if (typeFilter !== 'all' && chart.chartType !== typeFilter) return false
      if (q && !chartName(chart).toLowerCase().includes(q)) return false
      return true
    })
  }, [saved, query, typeFilter])

  const addSaved = async (chart: SavedChart) => {
    setAdding(chart.id)
    try {
      await api.addDashboardChart(dashboardId, { savedChartId: chart.id })
      onAdded()
    } finally {
      setAdding(null)
    }
  }

  return (
    <aside className={styles.panel} aria-label="Agregar un gráfico">
      <div className={styles.panelHead}>
        <div>
          <h3 className={styles.panelTitle}>Agregar gráfico</h3>
          <p className={styles.panelSub}>Desde {folderName}</p>
        </div>
        <button type="button" className={styles.panelClose} onClick={onClose} aria-label="Cerrar">
          <Icon icon={X} size="sm" />
        </button>
      </div>

      <div className={styles.panelTabs}>
        <Segmented
          value={tab}
          onChange={(v) => setTab(v as Tab)}
          options={[
            { value: 'saved', label: 'Gráficos guardados' },
            { value: 'ask', label: 'Hacer una pregunta' },
          ]}
        />
      </div>

      <div className={styles.panelBody}>
        {tab === 'saved' && (
          <>
            {saved.length > 0 && (
              <div className={styles.pickerFilters}>
                <div className="field-wrap">
                  <Icon icon={MagnifyingGlass} size={16} className="field-icon" />
                  <input
                    className="field"
                    placeholder="Buscar gráficos…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    aria-label="Buscar gráficos guardados por nombre"
                  />
                </div>
                <select
                  className="field"
                  value={typeFilter}
                  onChange={(e) => setTypeFilter(e.target.value as ChartType | 'all')}
                  aria-label="Filtrar por tipo de gráfico"
                >
                  <option value="all">Todos los tipos</option>
                  {CHART_TYPES.map((t) => (
                    <option key={t.key} value={t.key}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {loading ? (
              <div className={styles.pickerList} aria-busy="true" aria-label="Cargando gráficos guardados">
                {Array.from({ length: 4 }, (_, i) => (
                  <div key={i} className={styles.pickerRow} aria-hidden="true">
                    <Skeleton variant="block" width={64} height={48} style={{ flex: 'none' }} />
                    <span className={styles.pickerText}>
                      <Skeleton width={`${60 + (i % 3) * 12}%`} height={13} style={{ marginBottom: 8 }} />
                      <Skeleton width="40%" height={10} />
                    </span>
                  </div>
                ))}
              </div>
            ) : saved.length === 0 ? (
              <EmptyState
                icon={PushPin}
                title="Todavía no hay gráficos guardados para esta carpeta"
                description="Guardá un gráfico desde una conversación sobre esta carpeta, o hacé una pregunta acá."
              />
            ) : filtered.length === 0 ? (
              <EmptyState
                icon={MagnifyingGlass}
                title="Ningún gráfico coincide"
                description="Probá con otra búsqueda o tipo."
                action={
                  <button
                    type="button"
                    className="btn btn-glass btn-sm"
                    onClick={() => {
                      setQuery('')
                      setTypeFilter('all')
                    }}
                  >
                    Borrar filtros
                  </button>
                }
              />
            ) : (
              <div className={styles.pickerList}>
                {filtered.map((chart) => {
                  const name = chartName(chart)
                  // `chart.result.chart` is the authority on the type — its
                  // own field is narrowed to the six drawable types, where
                  // `chart.chartType` (chosen at save time) is the wider,
                  // theoretically-'table' union `SavedChart` still carries.
                  const previewType = chart.result.chart?.chartType
                  return (
                    <button
                      key={chart.id}
                      type="button"
                      className={styles.pickerRow}
                      onClick={() => void addSaved(chart)}
                      disabled={adding !== null}
                    >
                      <span className={styles.pickerPreview}>
                        {previewType && (
                          <ChartPreview
                            chartType={previewType}
                            multiColor={previewType === 'pie' || specHasColorChannel(chart.result.chart?.spec)}
                          />
                        )}
                      </span>
                      <span className={styles.pickerText}>
                        <span className={styles.pickerQ}>{name}</span>
                        {name !== chart.question && (
                          <span className={styles.pickerSub}>{chart.question}</span>
                        )}
                        <span className={styles.pickerMeta}>
                          {chart.chartType} · guardado {chart.savedAt}
                        </span>
                      </span>
                    </button>
                  )
                })}
              </div>
            )}
          </>
        )}

        {tab === 'ask' && (
          <AskForChart
            dashboardId={dashboardId}
            folderId={folderId}
            folderName={folderName}
            onAdded={onAdded}
          />
        )}
      </div>
    </aside>
  )
}
