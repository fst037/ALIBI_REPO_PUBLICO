import type { CSSProperties, ReactNode } from 'react'
import { cx } from '../../lib/cx'
import { useLegal } from './legalContext'
import type { LegalDoc } from './legalParts'
import styles from './LegalModal.module.css'

export interface LegalLinkProps {
  doc: LegalDoc
  /** Clause to open at (a section id of the document). */
  section?: string
  children: ReactNode
  className?: string
  style?: CSSProperties
}

/**
 * Opens the Terms or the Privacy Policy in a modal over the current view —
 * never a navigation, so a half-filled form (sign-up, payment) stays as it was.
 * Looks like a link; it is a button because it does not go anywhere.
 */
export function LegalLink({ doc, section, children, className, style }: LegalLinkProps) {
  const { openLegal } = useLegal()
  return (
    <button
      type="button"
      className={cx(styles.link, className)}
      style={style}
      onClick={(e) => {
        // Inside a <label> (the sign-up consent) a click would also toggle the checkbox.
        e.preventDefault()
        e.stopPropagation()
        openLegal(doc, section)
      }}
    >
      {children}
    </button>
  )
}
