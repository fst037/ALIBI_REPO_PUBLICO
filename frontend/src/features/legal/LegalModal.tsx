import { useEffect, useRef } from 'react'
import { Eyebrow, Modal } from '../../components/ui'
import { cx } from '../../lib/cx'
import { TERMS_UPDATED, TERMS_VERSION } from '../../lib/legal'
import type { LegalDoc, LegalDocContent } from './legalParts'
import styles from './LegalModal.module.css'

export interface LegalModalProps {
  doc: LegalDoc
  content: LegalDocContent
  /** Clause to scroll to when opened. */
  section?: string
  onSwitch: (doc: LegalDoc) => void
  onClose: () => void
}

const TABS: { doc: LegalDoc; label: string }[] = [
  { doc: 'terms', label: 'Términos y condiciones' },
  { doc: 'privacy', label: 'Política de privacidad' },
]

/**
 * A legal document in a closable modal over whatever the user was doing:
 * numbered clauses with an index, and tabs to move between the two documents.
 * Closes with the X, Escape or a click outside (the shared `Modal`).
 *
 * The index scrolls inside the modal rather than using `#anchors`, which the
 * router would read as a navigation.
 */
export function LegalModal({ doc, content, section, onSwitch, onClose }: LegalModalProps) {
  const bodyRef = useRef<HTMLDivElement>(null)

  const scrollTo = (id: string) => {
    const target = bodyRef.current?.querySelector<HTMLElement>(`[data-section="${id}"]`)
    if (target && bodyRef.current) bodyRef.current.scrollTo({ top: target.offsetTop - 12, behavior: 'smooth' })
  }

  // Open at the requested clause; switching documents starts at the top.
  useEffect(() => {
    if (section) scrollTo(section)
    else bodyRef.current?.scrollTo({ top: 0 })
  }, [doc, section])

  return (
    <Modal open onClose={onClose} size="xl" aria-label={content.title} className={styles.panel}>
      <div className={styles.head}>
        <div className={styles.tabs} role="tablist" aria-label="Documentos legales">
          {TABS.map((t) => (
            <button
              key={t.doc}
              type="button"
              role="tab"
              aria-selected={doc === t.doc}
              className={cx(styles.tab, doc === t.doc && styles.current)}
              onClick={() => onSwitch(t.doc)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.scroll} ref={bodyRef}>
        <div className={styles.intro}>
          <Eyebrow>Legal</Eyebrow>
          <h2>{content.title}</h2>
          <div className={styles.meta}>
            Última actualización: {TERMS_UPDATED} · versión <span className="mono">{TERMS_VERSION}</span>
          </div>
          <div className={styles.lead}>{content.lead}</div>
        </div>

        <div className={styles.cols}>
          <nav className={styles.toc} aria-label="Índice">
            <div className={styles.tocLabel}>Índice</div>
            {content.sections.map((s, i) => (
              <button key={s.id} type="button" onClick={() => scrollTo(s.id)}>
                {i + 1}. {s.title}
              </button>
            ))}
          </nav>
          <article className={styles.body}>
            {content.sections.map((s, i) => (
              <section key={s.id} data-section={s.id} className={styles.section}>
                <h3>
                  {i + 1}. {s.title}
                </h3>
                {s.body}
              </section>
            ))}
          </article>
        </div>
      </div>
    </Modal>
  )
}
