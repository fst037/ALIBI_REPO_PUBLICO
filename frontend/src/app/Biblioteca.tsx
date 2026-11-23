import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate } from 'react-router-dom'
import {
  CalendarBlank,
  Check,
  CheckSquare,
  ChartBar,
  Database,
  DotsThree,
  FolderSimple,
  Hash,
  Percent,
  Sparkle,
  Star,
  TextAa,
  ToggleRight,
  Trash,
  UploadSimple,
  X,
} from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { AppShell } from '../components/layout/AppShell'
import { Button, DataTable, FILE_ICON, Icon, Modal, Pill, Skeleton, Spinner, TableSkeleton, Toast } from '../components/ui'
import type { PillTone } from '../components/ui'
import { DatasetFoldersModal } from '../features/folders/DatasetFoldersModal'
import { FolderBar } from '../features/folders/FolderBar'
import type { Scope } from '../features/folders/FolderBar'
import * as api from '../lib/api'
import { ApiError } from '../lib/api/http'
import type { ColumnType, Dataset, DatasetSchema, Folder, TablePreview } from '../types'
import { toColumns } from '../features/conversation/tableAdapter'
import { cx } from '../lib/cx'
import { relativeDate } from '../lib/format'
import { usePlan } from '../features/billing/usePlan'
import styles from './Biblioteca.module.css'
// Reused for the bulk "move to folder" picker's `.pickRow` — same look as
// the per-dataset folders modal, no reason to redefine it.
import pickerStyles from '../features/folders/folders.module.css'

const TYPE_ICON: Record<ColumnType, PhosphorIcon> = {
  Text: TextAa,
  Number: Hash,
  'Whole number': Hash,
  Date: CalendarBlank,
  Percentage: Percent,
  Boolean: ToggleRight,
}
// `color` is the same signal as `tone`, for the places that render the label as
// plain text rather than as a Pill — kept in one map so a dataset in error
// never reads green in the drawer while its card shows a red pill.
const STATUS: Record<Dataset['status'], { label: string; tone: PillTone; color: string }> = {
  processed: { label: 'Procesado', tone: 'ok', color: 'var(--ok)' },
  processing: { label: 'Procesando', tone: 'warn', color: 'var(--warn)' },
  // The table exists but nobody has confirmed its schema yet, so it can be
  // previewed and not queried — an action the user still owes us, not an error.
  awaiting_confirmation: { label: 'Sin confirmar', tone: 'warn', color: 'var(--warn)' },
  error: { label: 'Error', tone: 'danger', color: 'var(--danger)' },
}

const fmt = (n: number) => n.toLocaleString('en-US')
const primaryCount = (d: Dataset) => (d.rows != null ? `${fmt(d.rows)} filas` : `${d.pages ?? 0} páginas`)

interface ToastItem {
  id: number
  tone: 'ok' | 'err'
  title: string
}
const TOAST_TTL_MS = 3200

/** `.dcard`'s loading shape — same grid cell, so the layout doesn't jump when
 *  real cards swap in. Not interactive: no hover lift, no click target. */
function DatasetCardSkeleton() {
  return (
    <div className={styles.dcard} style={{ cursor: 'default', pointerEvents: 'none' }}>
      <div className="between">
        <Skeleton variant="block" width={40} height={40} style={{ borderRadius: 'var(--r-sm)' }} />
        <Skeleton variant="line" width={64} height={20} style={{ borderRadius: 'var(--r-pill)' }} />
      </div>
      <div>
        <Skeleton width="70%" height={14} style={{ marginBottom: 8 }} />
        <Skeleton width="45%" height={11} />
      </div>
      <div className="row" style={{ gap: 12 }}>
        <Skeleton width={54} height={11} />
        <Skeleton width={38} height={11} />
        <Skeleton width={46} height={11} />
        <Skeleton width={70} height={11} />
      </div>
      <div className={styles.cardActions}>
        <Skeleton variant="block" height={34} style={{ flex: 1 }} />
        <Skeleton variant="block" width={36} height={34} />
      </div>
    </div>
  )
}

export function Biblioteca() {
  const { plan } = usePlan()
  const datasetLimit = plan?.limits.datasets ?? null
  const navigate = useNavigate()
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [openId, setOpenId] = useState<string | null>(null)
  const [schema, setSchema] = useState<DatasetSchema | null>(null)
  const [scope, setScope] = useState<Scope>('all')
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [preview, setPreview] = useState<TablePreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  // Shown inside whichever confirmation is open. Destructive actions that fail
  // silently are worse than ones that fail loudly: the modal stays open either
  // way, and without this the user just sees a button that did nothing.
  const [actionError, setActionError] = useState<string | null>(null)
  // Only the *first* load — `refresh()` is called again after every mutation
  // (rename a folder, delete a dataset...), and re-showing the skeleton then
  // would blank out a grid the user is already looking at for no reason.
  const [loading, setLoading] = useState(true)
  // The dataset currently being dragged toward a folder tile — dims the
  // source card. The hover-highlight on the *target* tile is local state
  // inside `FolderBar` (it only needs to know "is something over me right
  // now", which native `dragenter`/`dragleave` already tell it); the payload
  // itself travels through the browser's `dataTransfer`, not React state.
  const [draggingId, setDraggingId] = useState<string | null>(null)
  // Feedback for the drag-and-drop attach itself — a spinner on the tile
  // that's mid-request, a brief success pulse once it lands, and a toast
  // either way. `attachDatasets` used to fail silently here; a user with no
  // visible confirmation has no way to tell a drop worked from one that did
  // nothing.
  const [pendingFolderId, setPendingFolderId] = useState<string | null>(null)
  const [succeededFolderId, setSucceededFolderId] = useState<string | null>(null)
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const toastSeq = useRef(0)

  const pushToast = useCallback((tone: ToastItem['tone'], title: string) => {
    const id = ++toastSeq.current
    setToasts((t) => [...t, { id, tone, title }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), TOAST_TTL_MS)
  }, [])

  // Folders
  const [folders, setFolders] = useState<Folder[]>([])
  const [organising, setOrganising] = useState<Dataset | null>(null)
  const [folderForm, setFolderForm] = useState<{ mode: 'create' | 'rename'; id?: string; name: string } | null>(null)
  const [folderError, setFolderError] = useState<string | null>(null)
  const [folderBusy, setFolderBusy] = useState(false)
  const [deleteFolder, setDeleteFolder] = useState<Folder | null>(null)

  // Per-card "..." options menu — portalled to <body>, positioned from the
  // trigger button's own rect. Same reasoning as `FolderBar`'s folder-tile
  // menu: `.page-scroll` clips anything extending past its box regardless of
  // the popover's own `position`, so nesting it in the card isn't an option.
  const [cardMenu, setCardMenu] = useState<{ datasetId: string; rect: DOMRect } | null>(null)
  const pageScrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!cardMenu) return
    const close = () => setCardMenu(null)
    const scrollEl = pageScrollRef.current
    scrollEl?.addEventListener('scroll', close, { passive: true })
    window.addEventListener('resize', close)
    return () => {
      scrollEl?.removeEventListener('scroll', close)
      window.removeEventListener('resize', close)
    }
  }, [cardMenu])

  // Multi-select: pick several datasets, then move or delete them together.
  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [movePickerOpen, setMovePickerOpen] = useState(false)
  const [bulkDeleteConfirm, setBulkDeleteConfirm] = useState(false)

  const toggleSelectMode = () => {
    setSelectMode((m) => !m)
    setSelectedIds(new Set())
  }

  const toggleSelected = (id: string) => {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const refresh = useCallback(async () => {
    const [d, f] = await Promise.all([api.listDatasets(), api.listFolders()])
    setDatasets(d)
    setFolders(f)
  }, [])

  useEffect(() => {
    void refresh().finally(() => setLoading(false))
  }, [refresh])

  useEffect(() => {
    if (!openId) return
    setSchema(null)
    setPreview(null)
    // Distinct from `preview === null`, which also means "fetched, and there
    // genuinely isn't one" (an unconfirmed or errored dataset has no table to
    // read yet) — without this flag a skeleton would show forever in that case
    // instead of just for the fetch window.
    setPreviewLoading(true)
    let alive = true
    void api.getSchema(openId).then((s) => alive && setSchema(s))
    // A peek at the real rows, not only the inferred types — it is the fastest
    // way for someone to tell whether this is the dataset they meant.
    void api
      .previewDataset(openId, 6)
      .then((p) => alive && setPreview(p))
      .finally(() => alive && setPreviewLoading(false))
    return () => {
      alive = false
    }
  }, [openId])

  const sel = datasets.find((d) => d.id === openId) ?? null
  const activeFolder = folders.find((f) => f.id === scope) ?? null
  // Filtered client-side: every dataset already carries its `folderIds`, so
  // switching folders is instant and doesn't refetch the library.
  const visible =
    scope === 'all'
      ? datasets
      : scope === 'fav'
        ? datasets.filter((d) => d.favorite)
        : datasets.filter((d) => (d.folderIds ?? []).includes(scope))

  const toggleFavorite = (d: Dataset, e: React.MouseEvent) => {
    e.stopPropagation()
    const next = !d.favorite
    // Optimistic: reflect the toggle immediately and persist best-effort.
    // Favorites are a low-stakes preference, so a backend hiccup shouldn't
    // undo the user's click in the UI.
    setDatasets((ds) => ds.map((x) => (x.id === d.id ? { ...x, favorite: next } : x)))
    void api.setDatasetFavorite(d.id, next).catch(() => {})
  }

  const askDelete = (id: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    setActionError(null)
    setDeleteId(id)
  }

  const goConsultar = (id: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    navigate(`/app?dataset=${id}`)
  }

  const goConfirm = (id: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    navigate(`/ingesta?dataset=${id}`)
  }

  const submitFolderForm = async () => {
    if (!folderForm) return
    const name = folderForm.name.trim()
    if (!name) return
    setFolderBusy(true)
    setFolderError(null)
    try {
      if (folderForm.mode === 'create') await api.createFolder(name)
      else if (folderForm.id) await api.renameFolder(folderForm.id, name)
      await refresh()
      setFolderForm(null)
    } catch (err) {
      setFolderError(
        err instanceof ApiError && err.code === 'folder_name_taken'
          ? 'Ya tenés una carpeta con ese nombre.'
          : 'No pudimos guardar la carpeta.',
      )
    } finally {
      setFolderBusy(false)
    }
  }

  const confirmDeleteFolder = async () => {
    if (!deleteFolder) return
    setFolderBusy(true)
    setActionError(null)
    try {
      await api.deleteFolder(deleteFolder.id)
      // Only the folder went away, so the library stays as it was apart from
      // the links — which is exactly what `refresh` will show.
      if (scope === deleteFolder.id) setScope('all')
      await refresh()
      setDeleteFolder(null)
    } catch {
      setActionError('No pudimos eliminar la carpeta. Probá de nuevo.')
    } finally {
      setFolderBusy(false)
    }
  }

  const dropDatasetOnFolder = async (folderId: string, datasetId: string) => {
    const folderName = folders.find((f) => f.id === folderId)?.name ?? 'la carpeta'
    const datasetName = datasets.find((d) => d.id === datasetId)?.name ?? 'El dataset'
    setPendingFolderId(folderId)
    try {
      // Idempotent (dropping onto a folder the dataset is already in is a
      // harmless no-op per the API), so there's no need to check membership
      // before calling it — the only real outcome to distinguish is
      // succeeded vs. failed, which the toast below reports either way.
      await api.attachDatasets(folderId, [datasetId])
      pushToast('ok', `“${datasetName}” se agregó a “${folderName}”`)
      setSucceededFolderId(folderId)
      setTimeout(() => setSucceededFolderId((id) => (id === folderId ? null : id)), 600)
    } catch {
      pushToast('err', `No pudimos agregar “${datasetName}” a “${folderName}”`)
    } finally {
      setPendingFolderId(null)
      await refresh()
    }
  }

  const detachFromFolder = async (datasetId: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    if (!activeFolder) return
    try {
      await api.detachDataset(activeFolder.id, datasetId)
      await refresh()
    } catch {
      // Nothing was removed, so the card is still where it was — re-syncing is
      // the honest correction.
      await refresh()
    }
  }

  const confirmDelete = async () => {
    if (!deleteId) return
    const id = deleteId
    setDeleting(true)
    setActionError(null)
    try {
      await api.deleteDataset(id)
      if (openId === id) setOpenId(null)
      setDeleteId(null)
      // A full refetch, not a local filter: the deleted dataset may have
      // belonged to one or more folders, and only the server knows their
      // resulting counts. A local `setDatasets` filter kept the grid correct
      // (it's derived from `datasets`) but left every folder tile's
      // `datasetCount` stale until the next unrelated refresh.
      await refresh()
    } catch {
      setActionError('No pudimos eliminar el dataset. Probá de nuevo.')
    } finally {
      setDeleting(false)
    }
  }

  const bulkMoveToFolder = async (folderId: string) => {
    const ids = [...selectedIds]
    const folderName = folders.find((f) => f.id === folderId)?.name ?? 'la carpeta'
    setMovePickerOpen(false)
    try {
      // One bulk call, not one per dataset — `attachDatasets` already takes a
      // list, and it's idempotent, so a dataset already in this folder is
      // just a no-op inside the same request rather than something to filter
      // out beforehand.
      await api.attachDatasets(folderId, ids)
      pushToast('ok', `${ids.length} dataset(s) movido(s) a “${folderName}”`)
      setSelectMode(false)
      setSelectedIds(new Set())
    } catch {
      pushToast('err', `No pudimos mover los datasets a “${folderName}”`)
    } finally {
      await refresh()
    }
  }

  const confirmBulkDelete = async () => {
    const ids = [...selectedIds]
    setDeleting(true)
    setActionError(null)
    try {
      // `allSettled`, not `all`: one failing delete must not stop the rest
      // from going through, and the user needs to know if some (not all)
      // succeeded rather than seeing a generic failure for the whole batch.
      const results = await Promise.allSettled(ids.map((id) => api.deleteDataset(id)))
      const failed = results.filter((r) => r.status === 'rejected').length
      if (failed > 0) {
        setActionError(
          failed === ids.length
            ? 'No pudimos eliminar los datasets seleccionados. Probá de nuevo.'
            : `Se eliminaron ${ids.length - failed} de ${ids.length}. El resto no se pudo eliminar — probá de nuevo.`,
        )
      } else {
        setBulkDeleteConfirm(false)
        setSelectMode(false)
        setSelectedIds(new Set())
      }
    } finally {
      setDeleting(false)
      await refresh()
    }
  }

  return (
    <AppShell title="Biblioteca" activeNav="biblioteca">
      <div className="page-scroll" ref={pageScrollRef}>
        <div className="page-pad">
          <div className="page-head">
            <h1 className="page-h">Biblioteca de datasets</h1>
            <p className="page-sub">Todos tus datos en un solo lugar. Tocá un dataset para ver su esquema o empezar a hacer preguntas.</p>
          </div>

          <div className={styles.top}>
            <div className={styles.limit}>
              <Icon icon={Database} color="var(--warn)" />
              <div style={{ lineHeight: 1.3 }}>
                {loading ? (
                  <>
                    <Skeleton width={100} height={12} style={{ marginBottom: 5 }} />
                    <Skeleton width={150} height={10} />
                  </>
                ) : (
                  <>
                    <div style={{ fontSize: 12.5, fontWeight: 500 }}>
                      {datasetLimit != null ? `${datasets.length} de ${datasetLimit} datasets` : `${datasets.length} datasets`}
                    </div>
                    <div className="hint" style={{ fontSize: 11 }}>
                      {datasetLimit == null
                        ? `Plan ${plan?.name ?? ''} · datasets ilimitados`
                        : datasets.length >= datasetLimit
                          ? `Llegaste al límite del plan ${plan?.name}`
                          : `Plan ${plan?.name}`}
                    </div>
                  </>
                )}
              </div>
            </div>
            <div className={styles.topActions}>
              <button type="button" className="btn btn-glass" onClick={toggleSelectMode}>
                <Icon icon={selectMode ? X : CheckSquare} size="sm" />
                {selectMode ? 'Cancelar selección' : 'Seleccionar'}
              </button>
              {/* Uploading from inside a folder lands the results there. */}
              <Link
                className="btn btn-primary"
                to={activeFolder ? `/ingesta?folder=${activeFolder.id}` : '/ingesta'}
              >
                <Icon icon={UploadSimple} size="sm" />
                {activeFolder ? `Subir a ${activeFolder.name}` : 'Subir dataset'}
              </Link>
            </div>
          </div>

          {selectMode && (
            <div className={styles.selectBar}>
              <Icon icon={CheckSquare} size="sm" />
              {selectedIds.size === 0
                ? 'Tocá los datasets que querés mover o eliminar.'
                : `${selectedIds.size} seleccionado(s)`}
              <div className={styles.selectBarActions}>
                <Button
                  variant="glass"
                  onClick={() => setMovePickerOpen(true)}
                  disabled={selectedIds.size === 0}
                >
                  <Icon icon={FolderSimple} size="sm" />Mover a carpeta
                </Button>
                <Button
                  variant="glass"
                  className={styles.danger}
                  onClick={() => {
                    setActionError(null)
                    setBulkDeleteConfirm(true)
                  }}
                  disabled={selectedIds.size === 0}
                >
                  <Icon icon={Trash} size="sm" />Eliminar
                </Button>
              </div>
            </div>
          )}

          <FolderBar
            folders={folders}
            scope={scope}
            onScope={setScope}
            totalCount={datasets.length}
            favoriteCount={datasets.filter((d) => d.favorite).length}
            onCreate={() => {
              setFolderError(null)
              setFolderForm({ mode: 'create', name: '' })
            }}
            onRename={(f) => {
              setFolderError(null)
              setFolderForm({ mode: 'rename', id: f.id, name: f.name })
            }}
            onDelete={(f) => {
              setActionError(null)
              setDeleteFolder(f)
            }}
            onDropDataset={(folderId, datasetId) => void dropDatasetOnFolder(folderId, datasetId)}
            pendingFolderId={pendingFolderId}
            succeededFolderId={succeededFolderId}
            loading={loading}
          />

          {loading ? (
            <div className={styles.grid}>
              {Array.from({ length: 8 }).map((_, i) => <DatasetCardSkeleton key={i} />)}
            </div>
          ) : visible.length === 0 ? (
            <div className={styles.empty}>
              <Icon icon={activeFolder ? FolderSimple : Star} size={32} color="var(--fg-4)" />
              <p>
                {activeFolder
                  ? `“${activeFolder.name}” todavía no tiene datasets. Agregá uno desde su tarjeta o subí uno nuevo.`
                  : scope === 'fav'
                    ? 'Todavía no marcaste ningún dataset como favorito.'
                    : 'Todavía no hay datasets.'}
              </p>
            </div>
          ) : (
            <div className={styles.grid}>
              {visible.map((d) => {
                const status = STATUS[d.status]
                const selected = selectedIds.has(d.id)
                return (
                  <div
                    key={d.id}
                    className={cx(styles.dcard, draggingId === d.id && styles.dragging, selectMode && selected && styles.selected)}
                    role="button"
                    tabIndex={0}
                    // Dragging a card to a folder tile and multi-selecting are
                    // two different ways of doing similar things — offering
                    // both gestures on the same card at once just invites the
                    // browser to misinterpret one as the other.
                    draggable={!selectMode}
                    onDragStart={(e) => {
                      // `text/plain` — the folder shelf reads it back with
                      // `getData('text/plain')`; a bare id needs nothing fancier.
                      e.dataTransfer.setData('text/plain', d.id)
                      e.dataTransfer.effectAllowed = 'copy'
                      setDraggingId(d.id)
                    }}
                    onDragEnd={() => setDraggingId(null)}
                    onClick={() => (selectMode ? toggleSelected(d.id) : setOpenId(d.id))}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        if (selectMode) toggleSelected(d.id)
                        else setOpenId(d.id)
                      }
                    }}
                  >
                    <div className="between">
                      {selectMode ? (
                        <span className={cx(styles.cardCheckbox, selected && styles.checked)}>
                          {selected && <Icon icon={Check} size={13} />}
                        </span>
                      ) : (
                        <div className={cx(styles.dcardIco, styles[d.fileType])}><Icon icon={FILE_ICON[d.fileType]} size={20} /></div>
                      )}
                      <div className={styles.cardTop}>
                        <button
                          type="button"
                          className={cx(styles.favBtn, d.favorite && styles.favOn)}
                          onClick={(e) => toggleFavorite(d, e)}
                          aria-pressed={d.favorite}
                          aria-label={d.favorite ? 'Quitar de favoritos' : 'Agregar a favoritos'}
                          title={d.favorite ? 'Quitar de favoritos' : 'Agregar a favoritos'}
                        >
                          <Icon icon={Star} size="sm" weight={d.favorite ? 'fill' : 'regular'} />
                        </button>
                        {/* `processed` is the default, expected state — spelling
                            it out on every single card is noise once a library
                            has more than a couple of datasets. The other states
                            are exceptions the user needs to actually notice, so
                            those keep the full label. */}
                        {d.status === 'processed' ? (
                          <span
                            className={styles.statusDot}
                            style={{ color: status.color }}
                            role="img"
                            aria-label={status.label}
                            title={status.label}
                          />
                        ) : (
                          <Pill tone={status.tone} dot>{status.label}</Pill>
                        )}
                      </div>
                    </div>
                    <div>
                      <div className={styles.dcardName}>{d.name}</div>
                      <div className="hint" style={{ fontSize: 11.5, marginTop: 3 }}>
                        {/* The source label identifies which table inside the
                            file this is — the only way to tell several datasets
                            from one upload apart. */}
                        {d.sourceLabel ?? `Actualizado ${relativeDate(d.updatedAt)}`}
                      </div>
                    </div>
                    <div className={styles.dcardStats}>
                      <span>{primaryCount(d)}</span>
                      {d.columns != null && <span>{d.columns} col.</span>}
                      <span>{d.sizeLabel}</span>
                      <span>Creado {relativeDate(d.createdAt)}</span>
                    </div>
                    {!selectMode && (
                      <div className={styles.cardActions}>
                        {d.status === 'awaiting_confirmation' ? (
                          <button type="button" className={cx(styles.consultBtn, styles.warn)} onClick={(e) => goConfirm(d.id, e)}>
                            <Icon icon={Check} size="sm" />Confirmar
                          </button>
                        ) : (
                          <button type="button" className={styles.consultBtn} onClick={(e) => goConsultar(d.id, e)}>
                            <Icon icon={Sparkle} size="sm" />Preguntar
                          </button>
                        )}
                        {/* Both "Organize into folders" and remove/delete used to
                            be their own icon buttons here — two 36px buttons per
                            card ate a third of the row on every single dataset.
                            One "..." trigger opens the same actions from a menu. */}
                        <button
                          type="button"
                          className={styles.delBtn}
                          onClick={(e) => {
                            e.stopPropagation()
                            setCardMenu({ datasetId: d.id, rect: e.currentTarget.getBoundingClientRect() })
                          }}
                          aria-label="Más opciones"
                          aria-expanded={cardMenu?.datasetId === d.id}
                        >
                          <Icon icon={DotsThree} size="sm" />
                        </button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {cardMenu &&
        (() => {
          const menuDataset = datasets.find((d) => d.id === cardMenu.datasetId)
          if (!menuDataset) return null
          return createPortal(
            <>
              <div className={styles.cardMenuScrim} onClick={() => setCardMenu(null)} />
              <div
                className={styles.cardMenu}
                role="menu"
                style={{ top: cardMenu.rect.bottom + 6, right: window.innerWidth - cardMenu.rect.right }}
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setCardMenu(null)
                    setOrganising(menuDataset)
                  }}
                >
                  <Icon icon={FolderSimple} size="sm" />Organizar en carpetas
                </button>
                {/* Inside a folder, the obvious action is "take it out of
                    here" — which removes the link, not the dataset. */}
                {activeFolder ? (
                  <button
                    type="button"
                    role="menuitem"
                    onClick={(e) => {
                      setCardMenu(null)
                      void detachFromFolder(menuDataset.id, e)
                    }}
                  >
                    <Icon icon={X} size="sm" />Quitar de {activeFolder.name}
                  </button>
                ) : (
                  <button
                    type="button"
                    role="menuitem"
                    className={styles.danger}
                    onClick={(e) => {
                      setCardMenu(null)
                      askDelete(menuDataset.id, e)
                    }}
                  >
                    <Icon icon={Trash} size="sm" />Eliminar dataset
                  </button>
                )}
              </div>
            </>,
            document.body,
          )
        })()}

      {sel && (
        <div className={styles.scrim} onClick={() => setOpenId(null)}>
          <div className={styles.drawer} onClick={(e) => e.stopPropagation()}>
            <div className={styles.drawerHead}>
              <div className={cx(styles.dcardIco, styles[sel.fileType])} style={{ width: 44, height: 44 }}><Icon icon={FILE_ICON[sel.fileType]} size={22} /></div>
              <div className="grow">
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>{sel.name}</h3>
                <div className="hint" style={{ marginTop: 4 }}>
                  {sel.sourceLabel ? `${sel.sourceLabel} · ` : ''}Creado {relativeDate(sel.createdAt)}
                  {/* Redundant the moment nothing has changed since upload —
                      which is most datasets, most of the time. */}
                  {sel.updatedAt !== sel.createdAt && ` · Actualizado ${relativeDate(sel.updatedAt)}`}
                </div>
              </div>
              <button
                type="button"
                className={cx(styles.favBtn, sel.favorite && styles.favOn)}
                onClick={(e) => toggleFavorite(sel, e)}
                aria-pressed={sel.favorite}
                aria-label={sel.favorite ? 'Quitar de favoritos' : 'Agregar a favoritos'}
                title={sel.favorite ? 'Quitar de favoritos' : 'Agregar a favoritos'}
              >
                <Icon icon={Star} size="sm" weight={sel.favorite ? 'fill' : 'regular'} />
              </button>
              <Button variant="ghost" iconOnly onClick={() => setOpenId(null)} aria-label="Cerrar"><Icon icon={X} size="sm" /></Button>
            </div>
            <div className={styles.drawerBody}>
              <div className={styles.metaGrid}>
                <div className={styles.metaBox}><div className={styles.metaV}>{primaryCount(sel).split(' ')[0]}</div><div className={styles.metaL}>{sel.rows != null ? 'Filas' : 'Páginas'}</div></div>
                <div className={styles.metaBox}><div className={styles.metaV}>{sel.columns ?? '—'}</div><div className={styles.metaL}>Columnas</div></div>
                <div className={styles.metaBox}><div className={styles.metaV}>{sel.sizeLabel}</div><div className={styles.metaL}>Tamaño</div></div>
                <div className={styles.metaBox}><div className={styles.metaV} style={{ color: STATUS[sel.status].color, fontSize: 15 }}>{STATUS[sel.status].label}</div><div className={styles.metaL}>Estado</div></div>
              </div>
              {sel.folderIds && sel.folderIds.length > 0 && (
                <>
                  <div className="sb-lab" style={{ padding: '0 0 8px' }}>Carpetas</div>
                  <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginBottom: 18 }}>
                    {sel.folderIds.map((id) => {
                      const f = folders.find((x) => x.id === id)
                      return f ? <span key={id} className="pill"><Icon icon={FolderSimple} size="sm" />{f.name}</span> : null
                    })}
                  </div>
                </>
              )}

              {previewLoading && (
                <>
                  <div className="sb-lab" style={{ padding: '0 0 10px' }}>Primeras filas</div>
                  <div style={{ marginBottom: 18 }}>
                    <TableSkeleton columns={schema?.columns.length || 4} rows={4} />
                  </div>
                </>
              )}
              {!previewLoading && preview && preview.rows.length > 0 && (
                <>
                  <div className="sb-lab" style={{ padding: '0 0 10px' }}>Primeras filas</div>
                  <div style={{ marginBottom: 18 }}>
                    <DataTable columns={toColumns(preview)} rows={preview.rows} />
                  </div>
                </>
              )}

              <div className="sb-lab" style={{ padding: '0 0 10px' }}>Esquema inferido</div>
              <div className={styles.schList}>
                {schema?.columns.map((c) => (
                  <div key={c.name} className={styles.schItem}>
                    <Icon icon={TYPE_ICON[c.type]} color="var(--fg-3)" />
                    <span className="grow mono" style={{ fontSize: 13 }}>{c.name}</span>
                    <span className={styles.schType}>{api.COLUMN_TYPE_LABELS[c.type]}</span>
                  </div>
                ))}
                {!schema &&
                  Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className={styles.schItem}>
                      <Skeleton variant="block" width={16} height={16} style={{ borderRadius: 4 }} />
                      <Skeleton className="grow" width={`${50 + (i % 3) * 15}%`} />
                      <Skeleton width={70} height={18} style={{ borderRadius: 'var(--r-pill)' }} />
                    </div>
                  ))}
              </div>
            </div>
            <div className={styles.drawerFoot}>
              {sel.status === 'awaiting_confirmation' ? (
                // `.consultBtn` + `.warn` rather than `btn btn-primary`: same
                // warn tone as the card's own "Confirm" and the "Unconfirmed"
                // pill, not the ordinary accent-coloured CTA.
                <Link className={cx(styles.consultBtn, styles.warn)} to={`/ingesta?dataset=${sel.id}`} style={{ justifyContent: 'center' }}><Icon icon={Check} size="sm" />Confirmar esquema</Link>
              ) : (
                <Link className="btn btn-primary grow" to={`/app?dataset=${sel.id}`} style={{ justifyContent: 'center' }}><Icon icon={Sparkle} size="sm" />Preguntar a este dataset</Link>
              )}
              <Link className="btn btn-glass" to={`/dashboards/${sel.id}`}><Icon icon={ChartBar} size="sm" />Ver dashboards</Link>
              <Button variant="glass" iconOnly aria-label="Organizar en carpetas" title="Organizar en carpetas" onClick={() => setOrganising(sel)}><Icon icon={FolderSimple} size="sm" /></Button>
              <Button variant="glass" iconOnly aria-label="Eliminar dataset" title="Eliminar dataset" onClick={() => askDelete(sel.id)} style={{ color: 'var(--danger)' }}><Icon icon={Trash} size="sm" /></Button>
            </div>
          </div>
        </div>
      )}

      <DatasetFoldersModal
        dataset={organising}
        folders={folders}
        onClose={() => setOrganising(null)}
        onChanged={() => void refresh()}
      />

      <Modal
        open={folderForm !== null}
        onClose={() => !folderBusy && setFolderForm(null)}
        aria-label={folderForm?.mode === 'rename' ? 'Renombrar carpeta' : 'Nueva carpeta'}
      >
        <div style={{ padding: '26px 26px 22px' }}>
          <h3 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 6px' }}>
            {folderForm?.mode === 'rename' ? 'Renombrar carpeta' : 'Nueva carpeta'}
          </h3>
          <p style={{ fontSize: 13, color: 'var(--fg-3)', margin: '0 0 18px', lineHeight: 1.5 }}>
            Agrupá datasets en un proyecto para consultarlos juntos.
          </p>
          <input
            className="field"
            autoFocus
            maxLength={80}
            placeholder="Ventas 2024"
            value={folderForm?.name ?? ''}
            onChange={(e) => setFolderForm((f) => (f ? { ...f, name: e.target.value } : f))}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void submitFolderForm()
            }}
          />
          {folderError && <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--danger)' }}>{folderError}</div>}
          <div className="row" style={{ gap: 10, justifyContent: 'flex-end', marginTop: 20 }}>
            <Button variant="glass" onClick={() => setFolderForm(null)} disabled={folderBusy}>Cancelar</Button>
            <Button
              variant="primary"
              onClick={() => void submitFolderForm()}
              disabled={folderBusy || !folderForm?.name.trim()}
            >
              {folderBusy ? <Spinner /> : folderForm?.mode === 'rename' ? 'Guardar' : 'Crear carpeta'}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={deleteFolder !== null}
        onClose={() => !folderBusy && setDeleteFolder(null)}
        showClose={false}
        aria-label="Confirmar eliminación de carpeta"
      >
        <div className={styles.confirm}>
          <div className={styles.confirmIco}><Icon icon={FolderSimple} size={22} /></div>
          <h3 className={styles.confirmTitle}>¿Eliminar “{deleteFolder?.name}”?</h3>
          {/* The reassurance is the point: this is the action users hesitate on,
              and the answer is that nothing is lost. */}
          <p className={styles.confirmText}>
            Solo se elimina la carpeta. Los {deleteFolder?.datasetCount ?? 0} dataset(s) que
            contiene siguen en tu biblioteca.
          </p>
          {actionError && <p className={styles.confirmText} style={{ color: 'var(--danger)' }}>{actionError}</p>}
          <div className={styles.confirmActions}>
            <Button variant="glass" onClick={() => setDeleteFolder(null)} disabled={folderBusy}>No</Button>
            <Button variant="glass" className={styles.danger} onClick={() => void confirmDeleteFolder()} disabled={folderBusy}>
              {folderBusy ? <Spinner /> : 'Sí, eliminar carpeta'}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={deleteId !== null} onClose={() => !deleting && setDeleteId(null)} showClose={false} aria-label="Confirmar eliminación">
        <div className={styles.confirm}>
          <div className={styles.confirmIco}><Icon icon={Trash} size={22} /></div>
          <h3 className={styles.confirmTitle}>¿Eliminar dataset?</h3>
          <p className={styles.confirmText}>Esto elimina el dataset, su esquema, sus conversaciones y sus gráficos guardados. Esta acción no se puede deshacer.</p>
          {actionError && <p className={styles.confirmText} style={{ color: 'var(--danger)' }}>{actionError}</p>}
          <div className={styles.confirmActions}>
            <Button variant="glass" onClick={() => setDeleteId(null)} disabled={deleting}>No</Button>
            <Button variant="glass" className={styles.danger} onClick={confirmDelete} disabled={deleting}>
              {deleting ? <Spinner /> : 'Sí, eliminar'}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={movePickerOpen}
        onClose={() => setMovePickerOpen(false)}
        aria-label="Mover a carpeta"
      >
        <div style={{ padding: '26px 26px 22px' }}>
          <h3 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 6px' }}>Mover a carpeta</h3>
          <p style={{ fontSize: 13, color: 'var(--fg-3)', margin: '0 0 18px', lineHeight: 1.5 }}>
            {selectedIds.size} dataset(s) seleccionado(s). Elegí la carpeta de destino — es un vínculo, no
            una copia, así que pueden seguir en otras carpetas.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 280, overflowY: 'auto' }}>
            {folders.length === 0 && (
              <div className="hint" style={{ padding: '14px 4px' }}>
                Todavía no tenés carpetas — creá una desde el estante de arriba.
              </div>
            )}
            {folders.map((f) => (
              <button
                key={f.id}
                type="button"
                className={pickerStyles.pickRow}
                onClick={() => void bulkMoveToFolder(f.id)}
              >
                <Icon icon={FolderSimple} size="sm" />
                <span className="grow" style={{ textAlign: 'left' }}>{f.name}</span>
                <span className="hint" style={{ fontSize: 11 }}>{f.datasetCount}</span>
              </button>
            ))}
          </div>
          <div className="row" style={{ gap: 10, justifyContent: 'flex-end', marginTop: 20 }}>
            <Button variant="glass" onClick={() => setMovePickerOpen(false)}>Cancelar</Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={bulkDeleteConfirm}
        onClose={() => !deleting && setBulkDeleteConfirm(false)}
        showClose={false}
        aria-label="Confirmar eliminación múltiple"
      >
        <div className={styles.confirm}>
          <div className={styles.confirmIco}><Icon icon={Trash} size={22} /></div>
          <h3 className={styles.confirmTitle}>¿Eliminar {selectedIds.size} dataset(s)?</h3>
          <p className={styles.confirmText}>
            Esto elimina los datasets seleccionados, sus esquemas, conversaciones y gráficos guardados.
            Esta acción no se puede deshacer.
          </p>
          {actionError && <p className={styles.confirmText} style={{ color: 'var(--danger)' }}>{actionError}</p>}
          <div className={styles.confirmActions}>
            <Button variant="glass" onClick={() => setBulkDeleteConfirm(false)} disabled={deleting}>No</Button>
            <Button variant="glass" className={styles.danger} onClick={() => void confirmBulkDelete()} disabled={deleting}>
              {deleting ? <Spinner /> : 'Sí, eliminar'}
            </Button>
          </div>
        </div>
      </Modal>

      {toasts.length > 0 && (
        <div className={styles.toastStack} aria-live="polite">
          {toasts.map((t) => (
            <Toast key={t.id} tone={t.tone} title={t.title} className={cx('fade-up', styles.toastItem)} />
          ))}
        </div>
      )}
    </AppShell>
  )
}
