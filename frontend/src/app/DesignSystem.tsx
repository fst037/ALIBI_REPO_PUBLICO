import { useState } from 'react'
import { DotsThree, Lock, Sparkle, UploadSimple } from '@phosphor-icons/react'
import {
  AlibiLogo,
  AlibiMark,
  BarChart,
  Button,
  Card,
  ChartTypePicker,
  Chip,
  type ChartType,
  type Column,
  DataTable,
  DatasetCard,
  Eyebrow,
  Field,
  Icon,
  PlanCard,
  Pill,
  Segmented,
  Skeleton,
  Spinner,
  SqlBlock,
  Switch,
  ThinkingDots,
  Toast,
  UsageBar,
} from '../components/ui'
import styles from './DesignSystem.module.css'

const SQL = `SELECT region, SUM(sales) AS total
FROM sales
WHERE sale_date >= '2024-01-01'
GROUP BY region
ORDER BY total DESC;`

interface Row extends Record<string, unknown> {
  region: string
  sales: string
  delta: string
}
const COLS: Column<Row>[] = [
  { key: 'region', header: 'Región', align: 'left' },
  { key: 'sales', header: 'Ventas', align: 'num' },
  { key: 'delta', header: 'Δ mes ant.', align: 'num' },
]
const ROWS: Row[] = [
  { region: 'Buenos Aires', sales: '$1,284,500', delta: '+12.4%' },
  { region: 'Córdoba', sales: '$842,100', delta: '+6.1%' },
  { region: 'Santa Fe', sales: '$611,900', delta: '−2.3%' },
]
const BARS = [
  { label: 'Bs. As.', value: 1284500 },
  { label: 'Córdoba', value: 842100 },
  { label: 'Santa Fe', value: 611900 },
  { label: 'Mendoza', value: 498200 },
  { label: 'Salta', value: 292400 },
]

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className={styles.sec}>
      <div className={styles.secH}>
        <h2 className="section-title">{title}</h2>
        {hint && <span className="hint">{hint}</span>}
      </div>
      {children}
    </div>
  )
}

/** Dev-only catalog of the ALIBI UI primitives. */
export function DesignSystem() {
  const [billing, setBilling] = useState<'monthly' | 'annual'>('annual')
  const [dark, setDark] = useState(true)
  const [chart, setChart] = useState<ChartType>('bar')

  return (
    <div className={styles.root}>
      <div className={styles.head}>
        <h1 className="h1">Sistema de diseño</h1>
        <p className="muted" style={{ maxWidth: 560, marginTop: 10 }}>
          Primitivas construidas sobre <span className="mono" style={{ color: 'var(--accent)' }}>tokens.css</span>. Vidrio líquido sobre un lienzo gris oscuro, acento menta, Geist + JetBrains Mono.
        </p>
      </div>

      <Section title="Marca" hint="anillo = la conversación · barras = la respuesta · la alta se va">
        <div className={styles.grid}>
          <div className={styles.demo}>
            <div className={styles.demoLab}>Logotipo</div>
            <div className={styles.row} style={{ alignItems: 'center', gap: 26 }}>
              <AlibiLogo size={34} />
              <AlibiLogo size={22} glow={false} />
            </div>
          </div>
          <div className={styles.demo}>
            <div className={styles.demoLab}>Isotipo · escala</div>
            <div className={styles.row} style={{ alignItems: 'center', gap: 20 }}>
              <AlibiMark size={48} />
              <AlibiMark size={28} />
              <AlibiMark size={20} glow={false} />
              <AlibiMark size={16} glow={false} />
            </div>
          </div>
          <div className={styles.demo}>
            <div className={styles.demoLab}>Mono (hereda el color)</div>
            <div className={styles.row} style={{ alignItems: 'center', gap: 20 }}>
              <AlibiMark size={34} tone="mono" glow={false} />
              <span style={{ color: 'var(--fg-2)' }}><AlibiMark size={34} tone="mono" glow={false} /></span>
              <span style={{ color: 'var(--accent-ink)', background: 'var(--accent)', borderRadius: 'var(--r-sm)', padding: 6, display: 'inline-flex' }}>
                <AlibiMark size={34} tone="mono" glow={false} />
              </span>
            </div>
          </div>
        </div>
      </Section>

      <Section title="Botones">
        <div className={styles.grid}>
          <div className={styles.demo}><div className={styles.demoLab}>Variantes</div><div className={styles.row}><Button>Primario</Button><Button variant="glass">Vidrio</Button><Button variant="ghost">Fantasma</Button></div></div>
          <div className={styles.demo}><div className={styles.demoLab}>Con ícono</div><div className={styles.row}><Button><Icon icon={Sparkle} size="sm" />Preguntar</Button><Button variant="glass"><Icon icon={UploadSimple} size="sm" />Subir</Button></div></div>
          <div className={styles.demo}><div className={styles.demoLab}>Tamaños / estados</div><div className={styles.row}><Button size="sm">Pequeño</Button><Button variant="glass" iconOnly><Icon icon={DotsThree} size="sm" /></Button><Button disabled>Deshabilitado</Button></div></div>
        </div>
      </Section>

      <Section title="Chips · Pills · Eyebrow">
        <div className={styles.grid}>
          <div className={styles.demo}><div className={styles.demoLab}>Pills</div><div className={styles.row}><Pill tone="ok" dot>Procesado</Pill><Pill tone="warn" dot>Procesando</Pill><Pill tone="danger" dot>Error</Pill><Pill tone="accent" dot>Pro</Pill></div></div>
          <div className={styles.demo}><div className={styles.demoLab}>Chips</div><div className={styles.row}><Chip>sales_2024.csv</Chip><Chip>Favoritos</Chip></div></div>
          <div className={styles.demo}><div className={styles.demoLab}>Eyebrow</div><Eyebrow>Módulo A · Text-to-SQL</Eyebrow></div>
        </div>
      </Section>

      <Section title="Campos">
        <div className={styles.grid}>
          <div className={styles.demo}><div className={styles.demoLab}>Texto</div><Field label="Email" placeholder="vos@empresa.com" defaultValue="ana@empresa.com" /></div>
          <div className={styles.demo}><div className={styles.demoLab}>Con ícono / error</div><Field label="Contraseña" icon={Lock} type="password" defaultValue="123" error="Al menos 8 caracteres" /></div>
          <div className={styles.demo}><div className={styles.demoLab}>Switch · Segmentado</div><div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}><Switch checked={dark} onChange={setDark} label="Tema oscuro" showLabel /><Segmented options={[{ value: 'monthly', label: 'Mensual' }, { value: 'annual', label: 'Anual −20%' }]} value={billing} onChange={setBilling} /></div></div>
        </div>
      </Section>

      <Section title="Tarjetas · Datasets">
        <div className={styles.grid}>
          <Card><h3 className="card-title">Tarjeta de vidrio</h3><p className="card-sub">Superficie con desenfoque, borde y brillo</p></Card>
          <DatasetCard name="sales_2024.csv" fileType="csv" meta="12.480 filas · 2 MB · hace 3 días" status={{ tone: 'ok', label: 'Procesado', dot: true }} />
          <div className={styles.demo}><div className={styles.demoLab}>Uso del plan</div><div className="between" style={{ marginBottom: 10 }}><span className="hint">Consultas</span><span className="mono" style={{ fontSize: 12 }}>27 / 30</span></div><UsageBar value={27} max={30} /></div>
        </div>
      </Section>

      <Section title="Tabla de resultados">
        <DataTable columns={COLS} rows={ROWS} style={{ maxWidth: 560 }} />
      </Section>

      <Section title="Bloque SQL plegable">
        <div style={{ maxWidth: 560 }}><SqlBlock sql={SQL} /></div>
      </Section>

      <Section title="Gráfico + selector de tipo" hint="7 tipos admitidos">
        <ChartTypePicker value={chart} onChange={setChart} auto="bar" />
        <Card style={{ maxWidth: 560, marginTop: 16 }}><BarChart data={BARS} /></Card>
      </Section>

      <Section title="Retroalimentación">
        <div className={styles.grid}>
          <div className={styles.demo}><div className={styles.demoLab}>Skeleton</div><div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}><Skeleton width="70%" /><Skeleton width="90%" /><Skeleton variant="block" height={60} /></div></div>
          <div className={styles.demo}><div className={styles.demoLab}>Cargadores</div><div className={styles.row}><ThinkingDots /><Spinner /></div></div>
          <Toast tone="ok" title="Archivo procesado">sales_2024.csv está listo para consultar.</Toast>
        </div>
      </Section>

      <Section title="Planes">
        <div className={styles.grid} style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))' }}>
          <PlanCard name="FREE" price="$ 0" per="/mes" features={['3 datasets', '20 consultas/mes', '1.000 filas/consulta']} cta={<Button variant="glass" style={{ justifyContent: 'center' }}>Plan actual</Button>} />
          <PlanCard name="PRO" featured badge="Recomendado" price="$ 31.000" per="/mes + IVA" features={['Datasets ilimitados', '250 consultas/mes', '5.000 filas/consulta']} cta={<Button style={{ justifyContent: 'center' }}>Elegir Pro</Button>} />
        </div>
      </Section>
    </div>
  )
}
