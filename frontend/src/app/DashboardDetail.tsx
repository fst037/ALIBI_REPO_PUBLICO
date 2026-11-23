import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowClockwise, ArrowLeft, FolderSimple, Plus, SquaresFour, Trash } from '@phosphor-icons/react'
import { AppShell } from '../components/layout/AppShell'
import { Button, EmptyState, Icon, Skeleton } from '../components/ui'
import * as api from '../lib/api'
import type { ChartLayout, DashboardDetail as Board } from '../types'
import { DashboardGrid } from '../features/dashboards/DashboardGrid'
import { AddChartPanel } from '../features/dashboards/AddChartPanel'
import { cx } from '../lib/cx'
import styles from '../features/dashboards/dashboards.module.css'

/**
 * One dashboard: a grid of charts over its folder, recomputed on open.
 *
 * The board fetch returns *ideas* — `sql` + spec + position, no rows. Each card
 * then fetches its own data, so a chart whose query has gone stale marks itself
 * and the rest of the board still works. "Refresh" re-runs them all without
 * reloading the page, which is the point of a board that recomputes: the data
 * moved, and you want to see it move.
 *
 * "Add chart" opens a docked panel on the right (`AddChartPanel`) rather than
 * a modal — the grid reflows to share the width with it instead of being
 * covered, the way VSCode's Copilot panel shares the window with the editor.
 */
export function DashboardDetail() {
  const { dashboardId = '' } = useParams()
  const navigate = useNavigate()
  const [board, setBoard] = useState<Board | null>(null)
  const [loading, setLoading] = useState(true)
  const [panelOpen, setPanelOpen] = useState(false)
  const [refreshToken, setRefreshToken] = useState(0)
  const [notFound, setNotFound] = useState(false)

  const load = useCallback(() => {
    let alive = true
    setLoading(true)
    void api
      .getDashboard(dashboardId)
      .then((next) => alive && setBoard(next))
      .catch(() => alive && setNotFound(true))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [dashboardId])

  useEffect(() => load(), [load])

  const persistLayout = useCallback(
    (layout: Record<string, ChartLayout>) => {
      // Optimistic: the cards must not jump back to their old places while the
      // request is in flight, and the server is only being told what the grid
      // already showed the user.
      setBoard((current) =>
        current
          ? {
              ...current,
              charts: current.charts.map((c) => ({ ...c, layout: layout[c.id] ?? c.layout })),
            }
          : current,
      )
      void api.setDashboardLayout(dashboardId, layout).catch(() => load())
    },
    [dashboardId, load],
  )

  const removeChart = useCallback(
    (chartId: string) => {
      setBoard((current) =>
        current ? { ...current, charts: current.charts.filter((c) => c.id !== chartId) } : current,
      )
      void api.removeDashboardChart(dashboardId, chartId).catch(() => load())
    },
    [dashboardId, load],
  )

  const removeBoard = async () => {
    if (!board) return
    if (!window.confirm(`¿Eliminar “${board.name}”? Sus gráficos se eliminan con él.`)) return
    await api.deleteDashboard(board.id)
    navigate('/dashboards')
  }

  if (notFound) {
    return (
      <AppShell title="Dashboards" activeNav="dashboards">
        <div className="page-scroll">
          <div className="page-pad">
            <EmptyState
              icon={SquaresFour}
              title="Ese dashboard no existe"
              description="Puede que haya sido eliminado."
              action={
                <Link className="btn btn-primary" to="/dashboards">
                  Volver a los dashboards
                </Link>
              }
            />
          </div>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell title={board?.name ?? 'Dashboard'} activeNav="dashboards">
      <div className={styles.workspace}>
        <div className={cx('page-scroll', styles.scrollArea)}>
          <div className="page-pad">
            <Link className={cx('btn', 'btn-glass', 'btn-sm', styles.back)} to="/dashboards">
              <Icon icon={ArrowLeft} size="sm" />Todos los dashboards
            </Link>

            <div className="page-head">
              <h1 className="page-h">{board?.name ?? '…'}</h1>
              <p className="page-sub">
                <Icon icon={FolderSimple} size={14} /> {board?.folderName}  ·  los gráficos se ejecutan con
                los datos de esta carpeta tal como están ahora
              </p>
            </div>

            <div className={styles.boardBar}>
              <Button
                variant="primary"
                onClick={() => setPanelOpen((v) => !v)}
                disabled={!board}
                aria-pressed={panelOpen}
              >
                <Icon icon={Plus} size="sm" />Agregar gráfico
              </Button>
              <Button
                variant="glass"
                onClick={() => setRefreshToken((t) => t + 1)}
                disabled={!board || board.charts.length === 0}
              >
                <Icon icon={ArrowClockwise} size="sm" />Actualizar
              </Button>
              <span className="spacer" />
              <Button variant="ghost" onClick={() => void removeBoard()} disabled={!board}>
                <Icon icon={Trash} size="sm" />Eliminar
              </Button>
            </div>

            {loading && !board ? (
              <div
                style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 16 }}
                aria-busy="true"
                aria-label="Cargando dashboard"
              >
                {Array.from({ length: 4 }, (_, i) => (
                  <Skeleton key={i} variant="block" height={320} />
                ))}
              </div>
            ) : board && board.charts.length === 0 ? (
              <div className="card">
                <EmptyState
                  icon={SquaresFour}
                  title="Este dashboard está vacío"
                  description="Agregá un gráfico que guardaste desde una conversación sobre esta carpeta, o hacé una pregunta nueva acá mismo."
                  action={
                    <Button variant="primary" onClick={() => setPanelOpen(true)}>
                      <Icon icon={Plus} size="sm" />Agregar gráfico
                    </Button>
                  }
                />
              </div>
            ) : board ? (
              <DashboardGrid
                dashboardId={board.id}
                charts={board.charts}
                refreshToken={refreshToken}
                onLayoutChange={persistLayout}
                onRemove={removeChart}
              />
            ) : null}
          </div>
        </div>

        {board && panelOpen && (
          <AddChartPanel
            dashboardId={board.id}
            folderId={board.folderId}
            folderName={board.folderName}
            onClose={() => setPanelOpen(false)}
            onAdded={load}
          />
        )}
      </div>
    </AppShell>
  )
}
