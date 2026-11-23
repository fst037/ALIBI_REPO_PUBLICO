import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  ChartBar,
  ChartLine,
  ChartLineUp,
  ChartPie,
  CheckCircle,
  FileCsv,
  Gauge,
  Lightning,
  LockSimple,
  PlayCircle,
  Plus,
  Sparkle,
  Table,
  Wallet,
} from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import type { BillingCycle, Plan } from '../types'
import { AlibiMark, type Column, DataTable, Eyebrow, Icon, Pill, PlanCard, Segmented, SqlBlock } from '../components/ui'
import { cx } from '../lib/cx'
import * as api from '../lib/api'
import { formatArs } from '../lib/format'
import { SUPPORT_EMAIL } from '../lib/legal'
import { priceRuleNote } from '../features/billing/priceNote'
import styles from './Landing.module.css'

/* ---- Static marketing content ---- */

interface Step {
  n: string
  mod: string
  h: string
  p: string
}
const STEPS: Step[] = [
  {
    n: '1',
    mod: 'Módulo B · ETL liviano',
    h: 'Conectá tus datos',
    p: 'Soltá un CSV, Excel o PDF. ALIBI infiere el esquema — nombres, tipos, relaciones — y te deja confirmarlo antes de empezar.',
  },
  {
    n: '2',
    mod: 'Módulo A · Text-to-SQL',
    h: 'Preguntá en lenguaje natural',
    p: 'Escribí lo que querés saber como se lo dirías a un colega. Convertimos tu pregunta en SQL seguro y te devolvemos la tabla con una explicación.',
  },
  {
    n: '3',
    mod: 'Módulo C · Auto-visualización',
    h: 'Visualización automática',
    p: 'Elegimos el gráfico que mejor cuenta la respuesta, sin que tengas que configurar nada.',
  },
]

interface UseCase {
  icon: PhosphorIcon
  h: string
  p: string
}
const USE_CASES: UseCase[] = [
  { icon: ChartLineUp, h: 'Analistas comerciales', p: 'Ventas por región, producto o período sin esperar al equipo de datos.' },
  { icon: Wallet, h: 'Finanzas', p: 'Combiná costos, ingresos y presupuestos directo desde tus exportaciones contables.' },
  { icon: Gauge, h: 'Operaciones', p: 'Tiempos de ciclo, volúmenes y cuellos de botella a partir de los reportes que ya tenés.' },
]

interface Faq {
  q: string
  a: string
}
const FAQS: Faq[] = [
  {
    q: '¿Necesito saber SQL o programar?',
    a: 'No. Escribís tu pregunta en lenguaje natural y ALIBI genera el SQL por vos. Podés mirar el código si tenés curiosidad, pero nunca hace falta.',
  },
  {
    q: '¿Qué archivos puedo subir?',
    a: 'CSV, Excel (XLSX) y PDF que contengan tablas. ALIBI infiere el esquema automáticamente y te deja corregir nombres o tipos de columnas antes de confirmar.',
  },
  {
    q: '¿ALIBI puede modificar mis datos?',
    a: 'No. Toda consulta es de solo lectura. Si una pregunta escribiera o borrara datos, el sistema la bloquea y te explica por qué en lenguaje claro.',
  },
  {
    q: '¿Puedo combinar varios archivos en una misma pregunta?',
    a: 'Sí. Cada conversación trabaja sobre una carpeta: poné los archivos juntos en la misma carpeta y preguntá sobre todos ellos.',
  },
  {
    q: '¿Qué diferencia hay entre una respuesta Rápida y una Profunda?',
    a: 'La Rápida resuelve la pregunta en pocos pasos y responde enseguida: alcanza para la mayoría de las preguntas del día a día. La Profunda investiga más (hasta seis consultas sobre tus datos) y redacta la respuesta con un modelo más capaz, para preguntas que necesitan cruzar varios datos. El plan Free responde en modo Rápido; con Pro elegís el modo en cada conversación o en cada pregunta, dentro de tus 250 consultas del mes (las consultas adicionales se responden en modo Rápido). El precio es el mismo en los dos modos.',
  },
  {
    q: '¿Qué pasa si en un mes necesito más de 250 consultas?',
    a: 'Con Pro no tenés que cambiar de plan. Activás las consultas adicionales en tu cuenta, fijás cuánto querés gastar como máximo en el mes y cada consulta extra se cobra por separado, a mes vencido. Te avisamos cuando llegás al 80 % de tu cupo. Así pagás el volumen de tu temporada alta solo el mes en que lo usás, y no todo el año.',
  },
  {
    q: '¿Los precios incluyen IVA? ¿Me dan factura?',
    a: 'Los precios están en pesos argentinos y no incluyen IVA. Emitimos factura electrónica, así que si sos responsable inscripto podés computar el IVA como crédito fiscal.',
  },
  {
    q: '¿Por qué el precio en pesos puede cambiar?',
    a: 'Porque está atado al dólar oficial: cada precio tiene un valor de referencia en dólares y el precio en pesos se calcula con la cotización vigente. Cada factura toma el precio del día en que se emite. El pago anual se cobra por adelantado, al precio del día del pago, y lo mantiene durante los 12 meses. Si alguna vez cambiamos el valor de referencia en dólares, te avisamos con 30 días de anticipación.',
  },
  {
    q: '¿Qué pasa con mis datos si cancelo?',
    a: 'Podés dar de baja Pro cuando quieras desde tu cuenta, sin cargos posteriores, y seguís en el plan Free. Si eliminás la cuenta, tenés 30 días para exportar tus datos; después se borran en forma definitiva. Nunca usamos tus datos para entrenar modelos.',
  },
]

/* ---- Hero preview (static demo — same product components) ---- */

const PREVIEW_SQL = `SELECT region, SUM(ventas) AS ventas
FROM ventas
WHERE fecha_venta >= '2024-01-01'
GROUP BY region
ORDER BY ventas DESC
LIMIT 5;`

interface PreviewRow extends Record<string, unknown> {
  region: string
  ventas: string
}
const PREVIEW_COLUMNS: Column<PreviewRow>[] = [
  { key: 'region', header: 'Región', align: 'left' },
  { key: 'ventas', header: 'Ventas', align: 'num' },
]
const PREVIEW_ROWS: PreviewRow[] = [
  { region: 'Buenos Aires', ventas: '$1.284.500' },
  { region: 'Córdoba', ventas: '$842.100' },
  { region: 'Santa Fe', ventas: '$611.900' },
]
const PREVIEW_BARS = [
  { h: 92, color: 'var(--data-1)', label: 'Bs.As.' },
  { h: 60, color: 'var(--data-2)', label: 'Córdoba' },
  { h: 44, color: 'var(--data-3)', label: 'Sta.Fe' },
  { h: 36, color: 'var(--data-4)', label: 'Mendoza' },
  { h: 28, color: 'var(--data-5)', label: 'Salta' },
]
const PREVIEW_CHIPS: { icon: PhosphorIcon; label: string; active?: boolean }[] = [
  { icon: ChartBar, label: 'Barras', active: true },
  { icon: ChartLine, label: 'Líneas' },
  { icon: ChartPie, label: 'Torta' },
  { icon: Table, label: 'Tabla' },
]

const BILLING_OPTIONS: { value: BillingCycle; label: string }[] = [
  { value: 'monthly', label: 'Mensual' },
  { value: 'annual', label: 'Anual · −20%' },
]

export function Landing() {
  const [billing, setBilling] = useState<BillingCycle>('annual')
  const [openFaq, setOpenFaq] = useState<number>(0)

  // Prices come from the public catalogue: they are pesos at the official
  // dollar rate, so they cannot be written into the page.
  const [pro, setPro] = useState<Plan | null>(null)
  useEffect(() => {
    let alive = true
    api
      .listPlans()
      .then((plans) => alive && setPro(plans.find((p) => p.id === 'pro') ?? null))
      .catch(() => {
        // The page still works without prices; the cards show a dash.
      })
    return () => {
      alive = false
    }
  }, [])

  const annual = billing === 'annual'
  const proPrice = pro ? formatArs(api.priceFor(pro, billing)) : '—'
  const perNote = annual ? '/mes + IVA · pago anual' : '/mes + IVA'
  const overagePrice = pro?.overagePricePerQuery != null ? formatArs(pro.overagePricePerQuery) : '—'

  return (
    <div className={styles.lp}>
      {/* Topbar */}
      <header className={styles.topbar}>
        <div className={styles.wrap}>
          <div className={styles.topbarIn}>
            <div className={styles.brand}>
              <AlibiMark size={28} />ALIBI
            </div>
            <nav className={styles.navlinks}>
              <a className={styles.navlink} href="#como">Cómo funciona</a>
              <a className={styles.navlink} href="#planes">Planes</a>
              <a className={styles.navlink} href="#faq">FAQ</a>
            </nav>
            <div className="row" style={{ gap: 10 }}>
              <Link className="btn btn-ghost" to="/login">Iniciar sesión</Link>
              <Link className="btn btn-primary" to="/registro">
                <Icon icon={Sparkle} size="sm" />Empezar gratis
              </Link>
            </div>
          </div>
        </div>
      </header>

      {/* Hero */}
      <div className={styles.wrap}>
        <section className={styles.hero}>
          <Pill tone="accent" dot>BI no-code asistido por IA</Pill>
          <h1 className={styles.heroH}>
            Preguntale a tus datos.<br />
            Obtené <span className={styles.g}>respuestas y gráficos
              al instante</span>.
          </h1>
          <p className={styles.heroSub}>
            Subí un archivo o elegí una carpeta y hacé tu pregunta. ALIBI escribe el SQL, te
            muestra la tabla y genera la visualización — sin fórmulas, sin código, sin un analista
            en el medio.
          </p>
          <div className={styles.heroCta}>
            <Link className={cx('btn', 'btn-primary', styles.btnLg)} to="/registro">
              <Icon icon={ArrowRight} size="sm" />Crear una cuenta gratis
            </Link>
            <a className={cx('btn', 'btn-glass', styles.btnLg)} href="#como">
              <Icon icon={PlayCircle} size="sm" />Ver cómo funciona
            </a>
          </div>
          <div className={styles.trust}>
            <span><Icon icon={CheckCircle} size="sm" />Sin tarjeta para empezar</span>
            <span><Icon icon={LockSimple} size="sm" />Acceso de solo lectura a tus datos</span>
            <span><Icon icon={FileCsv} size="sm" />CSV · Excel · PDF</span>
          </div>

          {/* Preview — reuses the real SQL panel, results table and chart primitives */}
          <div className={cx(styles.preview, 'glass', styles.pvConv, 'fade-up')}>
            <div className={styles.pvQ}>
              <div className="msg-user">¿Cuáles fueron las 5 regiones con más ventas en 2024?</div>
            </div>
            <div className={styles.pvA}>
              <div className={styles.pvAiHead}>
                <span className={styles.miniMark}>A</span>
                Esto es lo que encontré en{' '}
                <span className="mono" style={{ color: 'var(--accent)' }}>ventas_2024.csv</span>
              </div>
              <SqlBlock sql={PREVIEW_SQL} />
              <DataTable columns={PREVIEW_COLUMNS} rows={PREVIEW_ROWS} rowKey={(r) => r.region} />
              <div className="bar-chart" style={{ height: 130 }}>
                {PREVIEW_BARS.map((b) => (
                  <div className="bar-col" key={b.label}>
                    <div className="bar" style={{ height: `${b.h}%`, background: b.color }} />
                    <div className="bar-lab">{b.label}</div>
                  </div>
                ))}
              </div>
              <div className="chart-picker">
                {PREVIEW_CHIPS.map((c) => (
                  <span key={c.label} className={cx('chart-opt', c.active && 'active')}>
                    <Icon icon={c.icon} size="sm" />{c.label}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </section>
      </div>

      {/* How it works */}
      <div className={styles.wrap}>
        <section className={styles.sec} id="como">
          <div className={styles.secEye}><Eyebrow>Cómo funciona</Eyebrow></div>
          <h2 className={styles.secTitle}>De la pregunta al gráfico en tres pasos</h2>
          <p className={styles.secLead}>Sin planillas trabadas, sin consultas escritas a mano. ALIBI se ocupa de la parte técnica.</p>
          <div className={styles.steps}>
            {STEPS.map((s) => (
              <div className={cx('card', styles.step)} key={s.n}>
                <div className={styles.stepN}>{s.n}</div>
                <div className={styles.stepMod}>{s.mod}</div>
                <h3 className={styles.stepH}>{s.h}</h3>
                <p className={styles.stepP}>{s.p}</p>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* Use cases */}
      <div className={styles.wrap}>
        <section className={styles.sec} style={{ paddingTop: 20 }}>
          <div className={styles.secEye}><Eyebrow>Para quién es</Eyebrow></div>
          <h2 className={styles.secTitle}>Hecho para quienes deciden, no para quienes programan</h2>
          <div className={styles.uses}>
            {USE_CASES.map((u) => (
              <div className={cx('card', styles.use)} key={u.h}>
                <div className={styles.useIco}><Icon icon={u.icon} size={21} /></div>
                <h3 className={styles.useH}>{u.h}</h3>
                <p className={styles.useP}>{u.p}</p>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* Plans */}
      <div className={styles.wrap}>
        <section className={styles.sec} id="planes">
          <div className={styles.secEye}><Eyebrow>Planes</Eyebrow></div>
          <h2 className={styles.secTitle}>Empezá gratis. Pagá más solo el mes que lo necesites.</h2>
          <p className={styles.secLead}>Sin sorpresas: los límites son de volumen de datos y consultas, nunca de funciones ocultas. Y si en temporada alta te quedás corto, no cambiás de plan: pagás solo las consultas extra.</p>
          <div className={styles.billToggle}>
            <Segmented options={BILLING_OPTIONS} value={billing} onChange={setBilling} aria-label="Ciclo de facturación" />
          </div>
          <div className={styles.plans}>
            <PlanCard
              name="FREE"
              price="$ 0"
              per="/mes"
              tagline="Para probar con tus datos reales."
              features={['3 datasets', '20 consultas por mes', '1.000 filas por consulta', 'Archivos de hasta 5 MB', 'Historial de 7 días', 'Exportación a CSV', 'Respuestas en modo Rápido']}
              cta={<Link className="btn btn-glass" style={{ justifyContent: 'center', marginTop: 'auto' }} to="/registro">Empezar gratis</Link>}
            />
            <PlanCard
              name="PRO"
              featured
              badge="Recomendado"
              price={proPrice}
              per={perNote}
              tagline="Para el trabajo de todos los días."
              features={['Datasets ilimitados', '250 consultas por mes', '5.000 filas por consulta', 'Archivos de hasta 100 MB', 'Historial ilimitado', 'Exportación a CSV, PNG y PDF', 'Respuestas en modo Rápido y Profundo', 'Soporte por correo']}
              cta={
                <Link className="btn btn-primary" style={{ justifyContent: 'center', marginTop: 'auto' }} to="/registro">
                  <Icon icon={Lightning} size="sm" />Elegir Pro
                </Link>
              }
            />
            <PlanCard
              name="CONSULTAS ADICIONALES"
              price={overagePrice}
              per="/consulta + IVA"
              tagline="Sobre Pro, solo el mes que las usás."
              features={[
                'Se activan al agotar las 250 consultas de Pro',
                'Vos fijás el tope de gasto del mes',
                'Aviso cuando llegás al 80 % del cupo',
                'Se cobran a mes vencido',
                'Sin cambiar de plan ni pagar el pico todo el año',
              ]}
              cta={<Link className="btn btn-glass" style={{ justifyContent: 'center', marginTop: 'auto' }} to="/registro">Empezar con Pro</Link>}
            />
          </div>
          <p className={cx('hint', styles.planNote)}>
            {pro ? priceRuleNote(pro) : 'Precios en pesos argentinos, más IVA, con factura electrónica, atados al dólar oficial.'}
          </p>
        </section>
      </div>

      {/* FAQ */}
      <div className={styles.wrap}>
        <section className={styles.sec} id="faq" style={{ paddingTop: 20 }}>
          <div className={styles.secEye}><Eyebrow>Preguntas frecuentes</Eyebrow></div>
          <h2 className={styles.secTitle}>Lo que suele preguntar la gente</h2>
          <div className={styles.faq}>
            {FAQS.map((f, i) => {
              const isOpen = openFaq === i
              return (
                <div className={styles.faqItem} key={f.q}>
                  <button
                    type="button"
                    className={styles.faqQ}
                    aria-expanded={isOpen}
                    onClick={() => setOpenFaq(isOpen ? -1 : i)}
                  >
                    {f.q}
                    <Icon icon={Plus} size="sm" className={cx(styles.faqIcon, isOpen && styles.faqIconOpen)} />
                  </button>
                  {isOpen && <div className={styles.faqA}>{f.a}</div>}
                </div>
              )
            })}
          </div>
        </section>
      </div>

      {/* Final CTA */}
      <div className={styles.wrap}>
        <div className={styles.ctaFinal}>
          <h2>Tu próxima respuesta ya está en tus datos</h2>
          <p className={styles.secLead} style={{ marginTop: 14 }}>
            Creá tu cuenta gratis y hacé tu primera pregunta en menos de dos minutos.
          </p>
          <div className={styles.heroCta} style={{ marginTop: 26 }}>
            <Link className={cx('btn', 'btn-primary', styles.btnLg)} to="/registro">
              <Icon icon={ArrowRight} size="sm" />Empezar gratis
            </Link>
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer className={styles.footer}>
        <div className={styles.wrap}>
          <div className={styles.footerIn}>
            <div className={styles.brand} style={{ fontSize: 14 }}>
              <AlibiMark size={22} glow={false} />ALIBI
            </div>
            <div className={styles.footerLinks}>
              <a href="#planes">Planes</a>
              <a href="#faq">FAQ</a>
              <Link to="/login">Iniciar sesión</Link>
              <a href={`mailto:${SUPPORT_EMAIL}`}>Soporte</a>
              {/* Real links, not the in-app modal: Google's consent screen needs the homepage to link to a public privacy URL. */}
              <Link to="/terminos">Términos</Link>
              <Link to="/privacidad">Privacidad</Link>
            </div>
            <span>© 2026 ALIBI</span>
          </div>
        </div>
      </footer>
    </div>
  )
}
