import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { X } from '@phosphor-icons/react'
import { cx } from '../../lib/cx'
import { Icon } from './Icon'
import styles from './Modal.module.css'

export interface ModalProps {
  open: boolean
  onClose: () => void
  children: ReactNode
  /** Panel max-width. */
  size?: 'md' | 'lg' | 'xl'
  /** Show the top-right close button (default true). */
  showClose?: boolean
  'aria-label'?: string
  className?: string
}

/**
 * Centered glass modal. Closes on backdrop click, Escape and the X button.
 * Consolidates the ad-hoc modal pattern used across screens.
 */
export function Modal({ open, onClose, children, size = 'md', showClose = true, 'aria-label': ariaLabel, className }: ModalProps) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className={styles.scrim} onClick={onClose}>
      <div
        className={cx(styles.panel, styles[size], className)}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        onClick={(e) => e.stopPropagation()}
      >
        {showClose && (
          <button type="button" className={styles.close} onClick={onClose} aria-label="Cerrar">
            <Icon icon={X} size="sm" />
          </button>
        )}
        {children}
      </div>
    </div>
  )
}
