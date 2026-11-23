import { useEffect, useState } from 'react'
import type { DragEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  Check,
  CloudArrowUp,
  Database,
  FolderSimple,
  MagnifyingGlass,
  Star,
  UploadSimple,
} from '@phosphor-icons/react'
import * as api from '../../lib/api'
import { setPendingUpload } from '../ingest/pendingUpload'
import type { ChatSource, Dataset, Folder } from '../../types'
import { Button, Card, FILE_ICON, Icon, Skeleton } from '../../components/ui'
import { cx } from '../../lib/cx'
import { usePlan } from '../billing/usePlan'
import { folderSource } from './source'
import styles from './conversation.module.css'


export interface SourcePickerProps {
  /** The chosen source — always a folder. */
  onStart: (source: ChatSource) => void
}

/**
 * Source-selection view: upload a new file, or pick the folder to ask about.
 *
 * **Folders only.** Picking loose datasets used to start a conversation over a
 * frozen list of ids; that source type no longer exists, on either side. The
 * folder is what gets sent, and the API re-resolves its contents on every read,
 * so a file added to it later joins a thread already in progress.
 *
 * Datasets are still listed — greyed, unselectable — rather than hidden. A user
 * looking for a file they know they uploaded should see it and be told where it
 * lives, instead of finding an empty list.
 */
export function SourcePicker({ onStart }: SourcePickerProps) {
  const { plan } = usePlan()
  const navigate = useNavigate()
  const [all, setAll] = useState<Dataset[]>([])
  const [query, setQuery] = useState('')
  const [drag, setDrag] = useState(false)
  const [folders, setFolders] = useState<Folder[]>([])
  const [foldersLoading, setFoldersLoading] = useState(true)
  // The chosen folder and the datasets inside it. The ids drive the footer
  // count; the folder is what actually travels.
  const [picked, setPicked] = useState<{ folder: Folder; datasets: Dataset[] } | null>(null)

  useEffect(() => {
    let alive = true
    void api.listDatasets().then((d) => alive && setAll(d))
    void api
      .listFolders()
      .then((f) => alive && setFolders(f))
      .finally(() => alive && setFoldersLoading(false))
    return () => {
      alive = false
    }
  }, [])

  // Folders are the source; the dataset list is search-only context.
  const folderList = query.trim()
    ? folders.filter((f) => f.name.toLowerCase().includes(query.toLowerCase()))
    : folders
  const datasetList = query.trim()
    ? all.filter((d) => d.name.toLowerCase().includes(query.toLowerCase()))
    : []

  const pickFolder = async (folder: Folder) => {
    const detail = await api.getFolder(folder.id)
    if (!detail) return
    setPicked(detail.datasets.length > 0 ? { folder, datasets: detail.datasets } : null)
  }

  const start = () => {
    if (!picked) return
    onStart(folderSource(picked.folder, picked.datasets))
  }

  const selCount = picked
    ? `Carpeta “${picked.folder.name}” · ${picked.datasets.length} dataset${picked.datasets.length > 1 ? 's' : ''}`
    : 'Elegí una carpeta para consultar'
  const goUpload = () => navigate('/ingesta')
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDrag(false)
    // The drop the user already made IS the upload. Park the file so /ingesta
    // starts it on arrival instead of opening idle and asking for the same
    // drag a second time. A drop carrying no file (dragged text, a folder)
    // still navigates, which is all this ever did.
    const chosen = e.dataTransfer.files?.[0]
    if (chosen) setPendingUpload(chosen)
    goUpload()
  }

  return (
    <div className={cx(styles.sourceWrap, 'fade-up')}>
      <div className={styles.sourceInner}>
        <div className={styles.sourceHead}>
          <h1 className={styles.sourceTitle}>¿Sobre qué datos querés preguntar?</h1>
          <p className={styles.sourceSub}>Subí un archivo nuevo o elegí la carpeta sobre la que querés preguntar.</p>
        </div>

        <div className={styles.sourceCols}>
          {/* Upload */}
          <Card className={styles.srcCard}>
            <div className={styles.srcCardH}>
              <div className={styles.srcCardIco}><Icon icon={UploadSimple} size={20} /></div>
              <div>
                <h3 className="card-title">Subir un archivo nuevo</h3>
                <p className="card-sub">CSV, Excel, PDF, Word o JSON{plan && ` · hasta ${plan.limits.maxFileMb} MB`}</p>
              </div>
            </div>
            <button
              type="button"
              className={cx(styles.dropzone, drag && styles.drag)}
              onClick={goUpload}
              onDragOver={(e) => {
                e.preventDefault()
                if (!drag) setDrag(true)
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={onDrop}
            >
              <Icon icon={CloudArrowUp} size={34} className={styles.dzIco} />
              <div>
                <div style={{ fontWeight: 500, fontSize: 14 }}>Soltá tu archivo acá</div>
                <div className="hint" style={{ marginTop: 4 }}>o hacé clic para elegir uno</div>
              </div>
              <div className="row" style={{ gap: 6, marginTop: 4 }}>
                <span className="pill">CSV</span>
                <span className="pill">XLSX</span>
                <span className="pill">PDF</span>
                <span className="pill">DOCX</span>
                <span className="pill">JSON</span>
              </div>
            </button>
          </Card>

          {/* Library */}
          <Card className={styles.srcCard}>
            <div className={styles.srcCardH}>
              <div className={styles.srcCardIco}><Icon icon={Database} size={20} /></div>
              <div>
                <h3 className="card-title">Elegir de tu biblioteca</h3>
                <p className="card-sub">Elegí una carpeta — todos sus archivos se consultan juntos</p>
              </div>
            </div>
            <div className={cx('field-wrap', styles.libSearch)}>
              <Icon icon={MagnifyingGlass} size={16} className="field-icon" />
              <input
                className="field"
                placeholder="Buscar carpetas…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className={styles.libList}>
              {foldersLoading &&
                Array.from({ length: 3 }, (_, i) => (
                  <div key={i} className={cx(styles.libRow, styles.libRowStatic)} aria-hidden="true">
                    <Skeleton variant="block" width={18} height={18} style={{ borderRadius: 5, flex: 'none' }} />
                    <Skeleton variant="block" width={32} height={32} style={{ borderRadius: 'var(--r-sm)', flex: 'none' }} />
                    <span className="grow" style={{ minWidth: 0 }}>
                      <Skeleton width={`${45 + i * 12}%`} height={13} style={{ marginBottom: 7 }} />
                      <Skeleton width="25%" height={10} />
                    </span>
                  </div>
                ))}
              {!foldersLoading && folderList.length === 0 && !query.trim() && (
                <div className="hint" style={{ padding: 14 }}>
                  Todavía no tenés carpetas. Creá una desde la Biblioteca — una
                  conversación trabaja sobre una carpeta, así que el archivo que
                  quieras consultar tiene que estar en una.
                </div>
              )}
              {folderList.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className={cx(styles.libRow, picked?.folder.id === f.id && styles.sel)}
                  onClick={() => void pickFolder(f)}
                  disabled={f.datasetCount === 0}
                  title={f.datasetCount === 0 ? 'Esta carpeta está vacía' : undefined}
                >
                  <span className={styles.libCheck}><Icon icon={Check} size={12} /></span>
                  <span className={styles.libIco}><Icon icon={FolderSimple} size="sm" /></span>
                  <span className="grow" style={{ minWidth: 0, textAlign: 'left' }}>
                    <span style={{ display: 'block', fontSize: 13, fontWeight: 500 }}>{f.name}</span>
                    <span className="hint" style={{ fontSize: 11 }}>
                      {f.datasetCount === 0 ? 'Vacía' : `${f.datasetCount} dataset${f.datasetCount > 1 ? 's' : ''}`}
                    </span>
                  </span>
                </button>
              ))}

              {/* Datasets matching the search, shown for orientation only. A
                  conversation cannot be opened over a loose file, so these are
                  not selectable — but a user hunting for a file they know they
                  uploaded should find it here and be told what to do, rather
                  than staring at an empty result. */}
              {datasetList.length > 0 && (
                <div className="hint" style={{ padding: '10px 14px 4px', fontSize: 11 }}>
                  Archivos coincidentes — agregá uno a una carpeta desde la Biblioteca para consultarlo
                </div>
              )}
              {datasetList.map((d) => (
                <div key={d.id} className={cx(styles.libRow, styles.libRowStatic)} aria-disabled="true">
                  <span className={styles.libCheck} />
                  <span className={cx(styles.libIco, styles[d.fileType])}>
                    <Icon icon={FILE_ICON[d.fileType]} size="sm" />
                  </span>
                  <span className="grow" style={{ minWidth: 0 }}>
                    <span style={{ display: 'block', fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                    <span className="hint" style={{ fontSize: 11 }}>
                      {d.status === 'processed' ? d.meta : 'Esquema aún sin confirmar'}
                    </span>
                  </span>
                  {d.favorite && <Icon icon={Star} size="sm" weight="fill" color="var(--warn)" />}
                </div>
              ))}
            </div>
            <div className={styles.sourceFoot}>
              <span className="hint">{selCount}</span>
              <Button variant="primary" disabled={!picked} onClick={start}>
                Empezar a preguntar<Icon icon={ArrowRight} size="sm" />
              </Button>
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}
