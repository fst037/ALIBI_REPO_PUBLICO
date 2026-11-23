import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  Article,
  BookOpenText,
  ChartBar,
  CreditCard,
  Database,
  EnvelopeSimple,
  Lightning,
  MagnifyingGlass,
  RocketLaunch,
  Scales,
  ShieldCheck,
} from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { AppShell } from '../components/layout/AppShell'
import { EmptyState, Field, Icon, LiveDot } from '../components/ui'
import { AI_NOTICE, SUPPORT_EMAIL } from '../lib/legal'
import { LegalLink } from '../features/legal/LegalLink'
import { useLegal } from '../features/legal/legalContext'
import type { LegalDoc } from '../features/legal/legalParts'
import styles from './Ayuda.module.css'

interface HelpDoc {
  title: string
  /** In-app page it opens; articles without one are not written yet. */
  to?: string
  /** A clause of the Terms or the Privacy Policy, opened in the legal modal. */
  legal?: { doc: LegalDoc; section?: string }
}
interface HelpCategory {
  name: string
  icon: PhosphorIcon
  docs: HelpDoc[]
}
const docs = (...titles: string[]): HelpDoc[] => titles.map((title) => ({ title }))

const CATEGORIES: HelpCategory[] = [
  { name: 'Primeros pasos', icon: RocketLaunch, docs: docs('Cómo subir tu primer dataset', 'Hacer tu primera pregunta en lenguaje natural', 'Entender la respuesta: tabla, SQL y gráfico') },
  { name: 'Datos y datasets', icon: Database, docs: docs('Formatos admitidos (CSV, Excel, PDF)', 'Confirmar y editar el esquema inferido', 'Preguntar sobre varios archivos de una misma carpeta', 'Límites de tamaño y filas por plan') },
  { name: 'Visualización', icon: ChartBar, docs: docs('Qué gráfico elige ALIBI y por qué', 'Cuándo una respuesta no trae gráfico', 'Exportar datos y gráficos') },
  {
    name: 'Cuenta y facturación',
    icon: CreditCard,
    docs: [
      ...docs('Diferencias entre Free y Pro', 'Consultas adicionales y tope de gasto'),
      { title: 'Por qué el precio en pesos sigue al dólar oficial', legal: { doc: 'terms', section: 'planes' } },
      { title: 'Cambiar o dar de baja tu plan', to: '/cuenta?tab=billing' },
      ...docs('Descargar tus facturas electrónicas'),
    ],
  },
  {
    name: 'Legal y privacidad',
    icon: Scales,
    docs: [
      { title: 'Términos y condiciones', legal: { doc: 'terms' } },
      { title: 'Política de privacidad', legal: { doc: 'privacy' } },
      { title: 'Qué datos llegan al modelo de IA', legal: { doc: 'privacy', section: 'archivos' } },
      { title: 'Acceder, corregir o eliminar tus datos personales', legal: { doc: 'privacy', section: 'derechos' } },
      { title: 'Alcance de las respuestas generadas con IA', legal: { doc: 'terms', section: 'resultados' } },
    ],
  },
]

export function Ayuda() {
  const { openLegal } = useLegal()
  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  const cats = CATEGORIES.map((c) => ({
    ...c,
    docs: c.docs.filter((d) => !q || d.title.toLowerCase().includes(q) || c.name.toLowerCase().includes(q)),
  })).filter((c) => c.docs.length > 0)

  return (
    <AppShell title="Ayuda y soporte">
      <div className="page-scroll">
        <div className="page-pad">
          <div className={styles.hero}>
            <h1>¿En qué podemos ayudarte?</h1>
            <p className="page-sub" style={{ marginTop: 8 }}>Guías, respuestas rápidas y contacto directo con nuestro equipo.</p>
            <div className={styles.search}>
              <Field icon={MagnifyingGlass} placeholder="Buscar en la ayuda…" value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
          </div>

          <div className={styles.cols}>
            <div>
              {cats.map((c) => (
                <div key={c.name} className={styles.cat}>
                  <div className={styles.catH}><Icon icon={c.icon} />{c.name}</div>
                  <div className={styles.docList}>
                    {c.docs.map((d) => {
                      const inner = (
                        <>
                          <Icon icon={Article} color="var(--fg-4)" />
                          <span>{d.title}</span>
                          <Icon icon={ArrowRight} className={styles.arw} />
                        </>
                      )
                      if (d.legal) {
                        const { doc, section } = d.legal
                        return (
                          <button key={d.title} type="button" className={styles.docItem} onClick={() => openLegal(doc, section)}>
                            {inner}
                          </button>
                        )
                      }
                      return d.to ? (
                        <Link key={d.title} className={styles.docItem} to={d.to}>{inner}</Link>
                      ) : (
                        <a key={d.title} className={styles.docItem} href="#">{inner}</a>
                      )
                    })}
                  </div>
                </div>
              ))}
              {cats.length === 0 && (
                <div className="card">
                  <EmptyState icon={MagnifyingGlass} title="Sin resultados" description={`No encontramos artículos para “${query}”. Escribinos y te ayudamos.`} />
                </div>
              )}
            </div>

            <div className="stack" style={{ gap: 16 }}>
              <div className={`card ${styles.contactCard}`}>
                <h3 className="card-title" style={{ marginBottom: 6 }}>Contactar a soporte</h3>
                <div className={styles.contactOpt}>
                  <div className={styles.contactIco}><Icon icon={EnvelopeSimple} size={19} /></div>
                  <div className="grow">
                    <div style={{ fontWeight: 600, fontSize: 13.5 }}>Correo electrónico</div>
                    <div className="hint" style={{ marginTop: 2 }}>
                      <a href={`mailto:${SUPPORT_EMAIL}`} style={{ color: 'var(--accent)' }}>{SUPPORT_EMAIL}</a> · respondemos en días hábiles
                    </div>
                  </div>
                </div>
              </div>

              <div className="card" style={{ background: 'linear-gradient(160deg, var(--accent-soft), var(--glass-fill) 70%)', borderColor: 'var(--accent-line)' }}>
                <div className="row" style={{ gap: 10, marginBottom: 8 }}>
                  <Icon icon={Lightning} weight="fill" size={20} color="var(--accent)" />
                  <span style={{ fontWeight: 600, fontSize: 14 }}>Soporte por correo</span>
                </div>
                <p className="hint" style={{ margin: '0 0 14px' }}>El plan Pro incluye soporte por correo para dudas de uso y facturación.</p>
                <Link className="btn btn-primary btn-sm" to="/cuenta?tab=billing" style={{ justifyContent: 'center', width: '100%' }}>Ver planes</Link>
              </div>

              <div className="card">
                <div className="row" style={{ gap: 9, marginBottom: 8 }}>
                  <Icon icon={ShieldCheck} color="var(--accent)" />
                  <span style={{ fontWeight: 600, fontSize: 14 }}>Información legal</span>
                </div>
                <p className="hint" style={{ margin: '0 0 12px', lineHeight: 1.55 }}>{AI_NOTICE}</p>
                <div className="stack" style={{ gap: 6 }}>
                  <LegalLink doc="terms" style={{ fontSize: 13, textAlign: 'left' }}>Términos y condiciones</LegalLink>
                  <LegalLink doc="privacy" style={{ fontSize: 13, textAlign: 'left' }}>Política de privacidad</LegalLink>
                </div>
              </div>

              <div className="card">
                <div className="row" style={{ gap: 9, marginBottom: 6 }}>
                  <Icon icon={BookOpenText} color="var(--accent)" />
                  <span style={{ fontWeight: 600, fontSize: 14 }}>Estado del servicio</span>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <LiveDot />
                  <span className="hint" style={{ color: 'var(--ok)' }}>Todos los sistemas operativos</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  )
}
