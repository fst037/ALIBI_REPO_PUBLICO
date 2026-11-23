import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChartBar, FolderSimple, Plus, SquaresFour } from '@phosphor-icons/react'
import { AppShell } from '../components/layout/AppShell'
import { Button, EmptyState, Field, Icon, Modal, Skeleton } from '../components/ui'
import * as api from '../lib/api'
import type { Dashboard, Folder } from '../types'
import styles from '../features/dashboards/dashboards.module.css'

/**
 * The dashboards list (flujo 4.4).
 *
 * A board is named and belongs to exactly one folder, the same scoping rule a
 * conversation follows. This replaced the implicit per-dataset gallery, which
 * had no name, no layout, no way to have two, and — because it stored answers
 * as snapshots — showed the numbers as they were on the day each chart was
 * pinned.
 */
export function Dashboards() {
  const navigate = useNavigate()
  const [boards, setBoards] = useState<Dashboard[]>([])
  const [folders, setFolders] = useState<Folder[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [folderId, setFolderId] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true)
    void Promise.all([api.listDashboards(), api.listFolders()])
      .then(([b, f]) => {
        if (!alive) return
        setBoards(b)
        setFolders(f)
      })
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  const create = async () => {
    if (!name.trim() || !folderId || saving) return
    setSaving(true)
    try {
      const board = await api.createDashboard(name.trim(), folderId)
      navigate(`/dashboards/${board.id}`)
    } finally {
      setSaving(false)
    }
  }

  const openCreate = () => {
    setName('')
    setFolderId(folders[0]?.id ?? '')
    setCreating(true)
  }

  return (
    <AppShell title="Dashboards" activeNav="dashboards">
      <div className="page-scroll">
        <div className="page-pad">
          <div className="page-head">
            <h1 className="page-h">Dashboards</h1>
            <p className="page-sub">
              Tableros de gráficos sobre una carpeta. Cada gráfico se vuelve a ejecutar con tus
              datos actuales cada vez que abrís uno.
            </p>
          </div>

          {loading ? (
            <div className={styles.boardGrid} aria-busy="true" aria-label="Cargando dashboards">
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} className={styles.boardCard} style={{ cursor: 'default' }}>
                  <Skeleton width={`${55 + (i % 3) * 12}%`} height={15} />
                  <Skeleton width="40%" height={12} />
                  <Skeleton width="28%" height={11} />
                </div>
              ))}
            </div>
          ) : boards.length === 0 ? (
            <div className="card">
              <EmptyState
                icon={SquaresFour}
                title="Todavía no hay dashboards"
                description={
                  folders.length === 0
                    ? 'Primero creá una carpeta en tu Biblioteca — un dashboard trabaja sobre una carpeta.'
                    : 'Creá uno sobre una carpeta y llenalo con gráficos que guardaste o con una pregunta nueva hecha desde ahí.'
                }
                action={
                  folders.length > 0 ? (
                    <Button variant="primary" onClick={openCreate}>
                      <Icon icon={Plus} size="sm" />Nuevo dashboard
                    </Button>
                  ) : undefined
                }
              />
            </div>
          ) : (
            <>
              <div className={styles.boardBar}>
                <span className="spacer" />
                <Button variant="primary" onClick={openCreate} disabled={folders.length === 0}>
                  <Icon icon={Plus} size="sm" />Nuevo dashboard
                </Button>
              </div>
              <div className={styles.boardGrid}>
                {boards.map((board) => (
                  <div
                    key={board.id}
                    className={styles.boardCard}
                    role="button"
                    tabIndex={0}
                    onClick={() => navigate(`/dashboards/${board.id}`)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        navigate(`/dashboards/${board.id}`)
                      }
                    }}
                  >
                    <span className={styles.boardName}>{board.name}</span>
                    <span className={styles.boardMeta}>
                      <Icon icon={FolderSimple} size={14} />
                      {board.folderName || 'Carpeta'}
                    </span>
                    <span className={styles.boardCount}>
                      <Icon icon={ChartBar} size={12} /> {board.chartCount}{' '}
                      {board.chartCount === 1 ? 'gráfico' : 'gráficos'}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        size="md"
        aria-label="Nuevo dashboard"
      >
        <div className={styles.detail}>
          <h3 className={styles.detailTitle}>Nuevo dashboard</h3>
          <Field
            label="Nombre"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Desempeño del 1.er trimestre"
          />
          <div className="field-group">
            <label className="field-label" htmlFor="dashboard-folder">Carpeta</label>
            <select
              id="dashboard-folder"
              className="field"
              value={folderId}
              onChange={(e) => setFolderId(e.target.value)}
            >
              {folders.map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {folder.name}
                </option>
              ))}
            </select>
            <span className="hint">
              Un dashboard trabaja sobre una carpeta y se resuelve cada vez que abrís el
              tablero — un archivo agregado después aparece en todos los gráficos.
            </span>
          </div>
          <div className={styles.askActions}>
            <Button variant="glass" onClick={() => setCreating(false)}>
              Cancelar
            </Button>
            <Button
              variant="primary"
              onClick={() => void create()}
              disabled={saving || !name.trim() || !folderId}
            >
              Crear
            </Button>
          </div>
        </div>
      </Modal>
    </AppShell>
  )
}
