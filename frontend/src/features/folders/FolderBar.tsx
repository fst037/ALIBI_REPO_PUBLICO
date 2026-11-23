import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { DotsThree, FolderSimple, PencilSimple, Plus, Star, Stack, Trash } from '@phosphor-icons/react'
import { Icon, Skeleton, Spinner } from '../../components/ui'
import type { Folder } from '../../types'
import { cx } from '../../lib/cx'
import styles from './folders.module.css'

/** `all` and `fav` are views over everything; anything else is a folder id. */
export type Scope = 'all' | 'fav' | string

export interface FolderBarProps {
  folders: Folder[]
  scope: Scope
  onScope: (scope: Scope) => void
  onCreate: () => void
  onRename: (folder: Folder) => void
  onDelete: (folder: Folder) => void
  /** A dataset card was dragged off the Biblioteca grid and dropped here. */
  onDropDataset: (folderId: string, datasetId: string) => void
  /** The folder whose `attachDatasets` call from a drop is still in flight —
   *  its tile shows a spinner instead of its icon. `null` when nothing is
   *  pending. Owned by Biblioteca, not this component: the request itself
   *  lives there, so only it knows when it actually resolves. */
  pendingFolderId: string | null
  /** The folder whose drop just succeeded — its tile pulses once. Also
   *  owned by Biblioteca, for the same reason: only it knows the drop
   *  actually landed, as opposed to merely having happened. */
  succeededFolderId: string | null
  /** True only for Biblioteca's *first* fetch. `totalCount`/`favoriteCount`
   *  are 0 before that resolves — indistinguishable from "you genuinely
   *  have no datasets" unless the All/Favorites tiles know to show a
   *  placeholder instead of trusting the count. Folder tiles don't need
   *  this: they simply don't exist yet while `folders` is still empty, so
   *  there's no zero to misread. */
  loading: boolean
  /** Count for the `all` tile; folders carry their own. */
  totalCount: number
  favoriteCount: number
}

// Matches the type Biblioteca's dataset cards write in `onDragStart` — plain
// `text/plain` rather than a custom MIME type, since the payload is nothing
// more than an id and every browser supports this one without a polyfill.
const DATASET_DRAG_TYPE = 'text/plain'

/**
 * Folder navigation for the Biblioteca — a horizontally scrolling row of
 * tiles sized like the dataset cards in the grid below, so a card dragged off
 * that grid reads as "the same kind of object" being dropped onto one of
 * these rather than a UI metaphor switch mid-gesture. One level deep: there
 * is no tree component here and no `parentId` anywhere, because folders
 * don't nest — that absence is the product decision, not a simplification.
 */
export function FolderBar({
  folders,
  scope,
  onScope,
  onCreate,
  onRename,
  onDelete,
  onDropDataset,
  pendingFolderId,
  succeededFolderId,
  loading,
  totalCount,
  favoriteCount,
}: FolderBarProps) {
  // The open menu's folder id *and* the trigger button's position at the
  // moment it opened. `.shelf` needs `overflow-y: hidden` to clip each
  // tile's background at its rounded corners — but that clips this menu
  // too, since CSS overflow has no way to say "clip the tile, not its
  // popover". Portalling the menu to `document.body` and positioning it
  // with the button's own `getBoundingClientRect()` sidesteps the clip
  // entirely, at the cost of needing that rect on hand.
  const [menu, setMenu] = useState<{ folderId: string; rect: DOMRect } | null>(null)
  // Which tile a drag is currently over, for the drop-target highlight. Local
  // to this component: the payload travels through the browser's own
  // `dataTransfer`, so nothing about the drag needs lifting to Biblioteca.
  const [dropHoverId, setDropHoverId] = useState<string | null>(null)
  const shelfRef = useRef<HTMLDivElement>(null)

  // The portalled menu has no DOM relationship to the button it's anchored
  // to, so nothing tells it that button just scrolled out from under it (or
  // that the viewport it was positioned against just changed size). Closing
  // on either is simpler and safer than tracking the button's position live.
  useEffect(() => {
    if (!menu) return
    const close = () => setMenu(null)
    const shelf = shelfRef.current
    shelf?.addEventListener('scroll', close, { passive: true })
    window.addEventListener('resize', close)
    return () => {
      shelf?.removeEventListener('scroll', close)
      window.removeEventListener('resize', close)
    }
  }, [menu])

  const activateOnKey = (onActivate: () => void) => (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onActivate()
    }
  }

  const menuFolder = menu ? folders.find((f) => f.id === menu.folderId) : undefined

  return (
    <div className={styles.shelf} ref={shelfRef}>
      <div
        role="button"
        tabIndex={0}
        className={cx(styles.tile, scope === 'all' && styles.on)}
        onClick={() => onScope('all')}
        onKeyDown={activateOnKey(() => onScope('all'))}
      >
        <div className={styles.tileTop}>
          <span className={styles.tileIco}><Icon icon={Stack} size="sm" /></span>
        </div>
        <span className={styles.tileName}>Todos</span>
        <span className={styles.tileCount}>
          {loading ? <Skeleton width={54} height={11} /> : `${totalCount} datasets`}
        </span>
      </div>

      <div
        role="button"
        tabIndex={0}
        className={cx(styles.tile, scope === 'fav' && styles.on)}
        onClick={() => onScope('fav')}
        onKeyDown={activateOnKey(() => onScope('fav'))}
      >
        <div className={styles.tileTop}>
          <span className={styles.tileIco}>
            <Icon icon={Star} size="sm" weight={scope === 'fav' ? 'fill' : 'regular'} />
          </span>
        </div>
        <span className={styles.tileName}>Favoritos</span>
        <span className={styles.tileCount}>
          {loading ? <Skeleton width={54} height={11} /> : `${favoriteCount} datasets`}
        </span>
      </div>

      {folders.map((f) => (
        <div
          key={f.id}
          role="button"
          tabIndex={0}
          className={cx(
            styles.tile,
            scope === f.id && styles.on,
            dropHoverId === f.id && styles.dropHover,
            succeededFolderId === f.id && styles.justDropped,
          )}
          onClick={() => onScope(f.id)}
          onKeyDown={activateOnKey(() => onScope(f.id))}
          onDragOver={(e) => {
            // Required for `onDrop` to fire at all — a dragover with no
            // `preventDefault` tells the browser this isn't a valid target.
            e.preventDefault()
            e.dataTransfer.dropEffect = 'copy'
          }}
          onDragEnter={(e) => {
            e.preventDefault()
            setDropHoverId(f.id)
          }}
          onDragLeave={(e) => {
            // Unlike `mouseleave`, `dragleave` has no "only when truly
            // leaving, not just crossing into a child" variant — it fires
            // every time the pointer crosses into the icon, name or count
            // inside this tile. Ignoring it unless the pointer actually left
            // the tile's own box is what stops the highlight flickering.
            if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
            setDropHoverId((id) => (id === f.id ? null : id))
          }}
          onDrop={(e) => {
            e.preventDefault()
            setDropHoverId(null)
            const datasetId = e.dataTransfer.getData(DATASET_DRAG_TYPE)
            if (!datasetId) return
            onDropDataset(f.id, datasetId)
          }}
        >
          <div className={styles.tileTop}>
            <span className={styles.tileIco}>
              {pendingFolderId === f.id ? (
                <Spinner />
              ) : (
                <Icon icon={FolderSimple} size="sm" weight={scope === f.id ? 'fill' : 'regular'} />
              )}
            </span>
            <button
              type="button"
              className={styles.tileMenuBtn}
              onClick={(e) => {
                e.stopPropagation()
                // Read the rect *before* the updater, not inside it: a
                // `setState` updater must be pure, and React re-invokes it a
                // second time in dev (StrictMode) to check exactly that. By
                // that second call the synthetic event has already been
                // recycled and `e.currentTarget` is `null` — reading it
                // inside the updater crashed on that replay, not the first,
                // genuine call.
                const rect = e.currentTarget.getBoundingClientRect()
                setMenu((m) => (m?.folderId === f.id ? null : { folderId: f.id, rect }))
              }}
              aria-label={`Opciones de ${f.name}`}
              aria-expanded={menu?.folderId === f.id}
            >
              <Icon icon={DotsThree} size="sm" />
            </button>
          </div>
          <span className={styles.tileName}>{f.name}</span>
          <span className={styles.tileCount}>{f.datasetCount} datasets</span>
        </div>
      ))}

      <button type="button" className={cx(styles.tile, styles.tileNew)} onClick={onCreate}>
        <Icon icon={Plus} size="sm" />
        Nueva carpeta
      </button>

      {menu && menuFolder &&
        createPortal(
          <>
            {/* Click-away layer rather than a document listener: it keeps the
                dismissal local to this component. */}
            <div className={styles.menuScrim} onClick={() => setMenu(null)} />
            <div
              className={styles.menu}
              role="menu"
              style={{ top: menu.rect.bottom + 6, right: window.innerWidth - menu.rect.right }}
            >
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenu(null)
                  onRename(menuFolder)
                }}
              >
                <Icon icon={PencilSimple} size="sm" />Renombrar
              </button>
              <button
                type="button"
                role="menuitem"
                className={styles.danger}
                onClick={() => {
                  setMenu(null)
                  onDelete(menuFolder)
                }}
              >
                <Icon icon={Trash} size="sm" />Eliminar carpeta
              </button>
            </div>
          </>,
          document.body,
        )}
    </div>
  )
}
