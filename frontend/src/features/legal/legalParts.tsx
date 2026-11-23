import type { ReactNode } from 'react'
import styles from './LegalModal.module.css'

/** The two legal documents. */
export type LegalDoc = 'terms' | 'privacy'

export interface LegalSection {
  /** Anchor, so a link can open the document at one clause. */
  id: string
  title: string
  body: ReactNode
}

export interface LegalDocContent {
  title: string
  lead: ReactNode
  sections: LegalSection[]
}

/** A highlighted paragraph inside a section (a notice the reader must not miss). */
export function LegalCallout({ children }: { children: ReactNode }) {
  return <p className={styles.callout}>{children}</p>
}
