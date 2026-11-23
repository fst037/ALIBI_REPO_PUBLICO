import { Link } from 'react-router-dom'
import { AlibiMark, Eyebrow } from '../components/ui'
import { TERMS_UPDATED, TERMS_VERSION } from '../lib/legal'
import type { LegalDoc } from '../features/legal/legalParts'
import { PRIVACY } from '../features/legal/privacyContent'
import { TERMS } from '../features/legal/termsContent'
import doc from '../features/legal/LegalModal.module.css'
import styles from './Legal.module.css'

/**
 * The Terms and the Privacy Policy as ordinary pages, at `/terminos` and
 * `/privacidad`. The same text as the in-app modal (`features/legal`), but with
 * a URL of its own: Google's consent screen requires a public privacy-policy
 * link to take "Continuar con Google" to production, and a visitor can read them without an account. Public, not
 * behind `RequireAuth`.
 */
export function Legal({ doc: which }: { doc: LegalDoc }) {
  const content = which === 'terms' ? TERMS : PRIVACY

  const scrollTo = (id: string) =>
    document.getElementById(`sec-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  return (
    <div className={styles.page}>
      <header className={styles.top}>
        <Link className={styles.brand} to="/">
          <AlibiMark size={26} />ALIBI
        </Link>
        <nav className={styles.tabs} aria-label="Documentos legales">
          <Link className={which === 'terms' ? styles.current : undefined} to="/terminos">
            Términos y condiciones
          </Link>
          <Link className={which === 'privacy' ? styles.current : undefined} to="/privacidad">
            Política de privacidad
          </Link>
        </nav>
      </header>

      <main className={styles.main}>
        <div className={doc.intro}>
          <Eyebrow>Legal</Eyebrow>
          <h1 className={styles.title}>{content.title}</h1>
          <div className={doc.meta}>
            Última actualización: {TERMS_UPDATED} · versión <span className="mono">{TERMS_VERSION}</span>
          </div>
          <div className={doc.lead}>{content.lead}</div>
        </div>

        <div className={doc.cols}>
          <nav className={doc.toc} aria-label="Índice">
            <div className={doc.tocLabel}>Índice</div>
            {content.sections.map((s, i) => (
              <button key={s.id} type="button" onClick={() => scrollTo(s.id)}>
                {i + 1}. {s.title}
              </button>
            ))}
          </nav>
          <article className={doc.body}>
            {content.sections.map((s, i) => (
              <section key={s.id} id={`sec-${s.id}`} className={doc.section}>
                <h2 className={styles.clause}>
                  {i + 1}. {s.title}
                </h2>
                {s.body}
              </section>
            ))}
          </article>
        </div>
      </main>
    </div>
  )
}
