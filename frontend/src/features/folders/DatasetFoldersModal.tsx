import { useEffect, useState } from 'react'
import { Check, FolderSimple, Plus } from '@phosphor-icons/react'
import { Button, Icon, Modal, Spinner } from '../../components/ui'
import * as api from '../../lib/api'
import { ApiError } from '../../lib/api/http'
import type { Dataset, Folder } from '../../types'
import { cx } from '../../lib/cx'
import styles from './folders.module.css'

export interface DatasetFoldersModalProps {
  dataset: Dataset | null
  folders: Folder[]
  onClose: () => void
  /** Called after any change so the caller can refresh counts and membership. */
  onChanged: () => void
}

/**
 * Attach/detach one dataset across folders.
 *
 * Checkboxes rather than a single-select or a "move" action, because the
 * relation is many-to-many: a dataset genuinely lives in several folders at
 * once, and each box is one link. Nothing here can delete a dataset.
 */
export function DatasetFoldersModal({ dataset, folders, onClose, onChanged }: DatasetFoldersModalProps) {
  const [busy, setBusy] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  // Mirrors the dataset's membership locally so each toggle shows immediately
  // instead of waiting for the parent to refetch.
  const [links, setLinks] = useState<string[]>(dataset?.folderIds ?? [])

  // Re-seeded whenever a different dataset is opened. The component stays
  // mounted while `dataset` is null (Biblioteca renders it unconditionally), so
  // the initialiser above only ever runs for the null case — without this the
  // checkmarks would show the *previous* dataset's folders, or none at all.
  const datasetId = dataset?.id
  useEffect(() => {
    if (datasetId) setLinks(dataset?.folderIds ?? [])
    // Only on identity change: re-running when the parent refetches would
    // clobber a toggle that hasn't round-tripped yet.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetId])

  if (!dataset) return null

  const toggle = async (folder: Folder) => {
    const linked = links.includes(folder.id)
    setBusy(folder.id)
    setError(null)
    try {
      if (linked) {
        await api.detachDataset(folder.id, dataset.id)
        setLinks((l) => l.filter((x) => x !== folder.id))
      } else {
        await api.attachDatasets(folder.id, [dataset.id])
        setLinks((l) => [...l, folder.id])
      }
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'No pudimos guardar el cambio.')
    } finally {
      setBusy(null)
    }
  }

  const create = async () => {
    const trimmed = name.trim()
    if (!trimmed) return
    setBusy('new')
    setError(null)
    try {
      // Created with the dataset already attached — one step, since that is
      // always why you make a folder from here.
      const folder = await api.createFolder(trimmed, [dataset.id])
      setLinks((l) => [...l, folder.id])
      setName('')
      setCreating(false)
      onChanged()
    } catch (err) {
      setError(
        err instanceof ApiError && err.code === 'folder_name_taken'
          ? 'Ya tenés una carpeta con ese nombre.'
          : 'No pudimos crear la carpeta.',
      )
    } finally {
      setBusy(null)
    }
  }

  return (
    <Modal open onClose={onClose} aria-label="Organizar en carpetas">
      <div className={styles.modal}>
        <h3 className={styles.modalTitle}>Organizar en carpetas</h3>
        <p className={styles.modalSub}>
          <span className="mono">{dataset.name}</span> can be in several folders at once. It's a
          link, not a copy.
        </p>

        <div className={styles.pickList}>
          {folders.length === 0 && !creating && (
            <div className="hint" style={{ padding: '14px 4px' }}>Todavía no tenés carpetas.</div>
          )}
          {folders.map((f) => {
            const linked = links.includes(f.id)
            return (
              <button
                key={f.id}
                type="button"
                className={cx(styles.pickRow, linked && styles.picked)}
                onClick={() => void toggle(f)}
                disabled={busy !== null}
                aria-pressed={linked}
              >
                <span className={styles.pickBox}>
                  {busy === f.id ? <Spinner /> : linked ? <Icon icon={Check} size={12} /> : null}
                </span>
                <Icon icon={FolderSimple} size="sm" weight={linked ? 'fill' : 'regular'} />
                <span className="grow" style={{ textAlign: 'left' }}>{f.name}</span>
                <span className="hint" style={{ fontSize: 11 }}>{f.datasetCount}</span>
              </button>
            )
          })}
        </div>

        {creating ? (
          <div className={styles.newRow}>
            <input
              className="field"
              autoFocus
              placeholder="Nombre de la carpeta"
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void create()
                if (e.key === 'Escape') setCreating(false)
              }}
            />
            <Button variant="primary" onClick={() => void create()} disabled={!name.trim() || busy === 'new'}>
              {busy === 'new' ? <Spinner /> : 'Crear'}
            </Button>
          </div>
        ) : (
          <Button variant="ghost" onClick={() => setCreating(true)}>
            <Icon icon={Plus} size="sm" />Nueva carpeta
          </Button>
        )}

        {error && <div className={styles.error}>{error}</div>}

        <div className={styles.modalFoot}>
          <Button variant="glass" onClick={onClose}>Listo</Button>
        </div>
      </div>
    </Modal>
  )
}
