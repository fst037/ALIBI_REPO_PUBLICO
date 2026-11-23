import { useEffect, useState } from 'react'
import { Check, FolderSimple, X } from '@phosphor-icons/react'
import * as api from '../../lib/api'
import type { Dataset, Folder } from '../../types'
import { Button, Icon, Skeleton } from '../../components/ui'
import { cx } from '../../lib/cx'
import styles from './conversation.module.css'


export interface SourceSwitchModalProps {
  open: boolean
  /** Folder id the conversation currently runs over. */
  current: string
  onClose: () => void
  onConfirm: (folder: Folder, datasets: Dataset[]) => void
}

/**
 * Change the active folder without losing the conversation.
 *
 * Folders, not datasets. This used to offer individual files and switch the
 * thread onto an ad-hoc selection of them; that source type no longer exists —
 * a conversation runs over one folder, re-resolved on every read.
 */
export function SourceSwitchModal({ open, current, onClose, onConfirm }: SourceSwitchModalProps) {
  const [folders, setFolders] = useState<Folder[]>([])
  const [loading, setLoading] = useState(true)
  const [sel, setSel] = useState<string>(current)

  useEffect(() => {
    if (!open) return
    setSel(current)
    let alive = true
    void api
      .listFolders()
      .then((f) => alive && setFolders(f))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!open) return null

  const confirm = async () => {
    const folder = folders.find((f) => f.id === sel)
    if (!folder) return
    const detail = await api.getFolder(folder.id)
    if (!detail) return
    onConfirm(folder, detail.datasets)
  }

  return (
    <div className={styles.scrim} onClick={onClose}>
      <div className={styles.switchModal} onClick={(e) => e.stopPropagation()}>
        <div className="between" style={{ marginBottom: 16 }}>
          <h3 className="card-title">Cambiar fuente de datos</h3>
          <Button variant="ghost" iconOnly onClick={onClose} aria-label="Cerrar"><Icon icon={X} size="sm" /></Button>
        </div>
        <p className="hint" style={{ margin: '-8px 0 16px' }}>Elegí otra carpeta. Tu conversación actual se conserva.</p>
        <div className={styles.libList} style={{ maxHeight: 280 }}>
          {loading &&
            Array.from({ length: 3 }, (_, i) => (
              <div key={i} className={styles.libRow} aria-hidden="true">
                <Skeleton variant="block" width={18} height={18} style={{ borderRadius: 5, flex: 'none' }} />
                <Skeleton variant="block" width={32} height={32} style={{ borderRadius: 'var(--r-sm)', flex: 'none' }} />
                <span className="grow" style={{ minWidth: 0 }}>
                  <Skeleton width={`${45 + i * 12}%`} height={13} style={{ marginBottom: 7 }} />
                  <Skeleton width="25%" height={10} />
                </span>
              </div>
            ))}
          {!loading && folders.length === 0 && (
            <div className="hint" style={{ padding: 14 }}>
              Todavía no tenés otras carpetas.
            </div>
          )}
          {folders.map((f) => (
            <button
              key={f.id}
              type="button"
              className={cx(styles.libRow, sel === f.id && styles.sel)}
              onClick={() => setSel(f.id)}
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
        </div>
        <Button
          variant="primary"
          onClick={() => void confirm()}
          disabled={!sel || sel === current}
          style={{ width: '100%', justifyContent: 'center', marginTop: 16 }}
        >
          Cambiar fuente
        </Button>
      </div>
    </div>
  )
}
