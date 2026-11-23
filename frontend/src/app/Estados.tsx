import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowClockwise,
  ArrowCounterClockwise,
  ClockUser,
  Lightning,
  MagnifyingGlass,
  PencilSimple,
  ShieldWarning,
  SignIn,
  Tray,
  UploadSimple,
  WifiSlash,
} from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { Button, Icon } from '../components/ui'
import { cx } from '../lib/cx'
import styles from './Estados.module.css'

function Cell({ tone, label, children }: { tone: string; label: string; children: ReactNode }) {
  return (
    <div className={styles.cell}>
      <div className={styles.lab}>
        <span className={styles.labDot} style={{ background: tone }} />
        <span className={styles.labT}>{label}</span>
      </div>
      <div className={styles.stage}>
        <div className={styles.inner}>{children}</div>
      </div>
    </div>
  )
}

function BigEmpty({ icon, ico, title, description, actions }: { icon: PhosphorIcon; ico: string; title: string; description: string; actions: ReactNode }) {
  return (
    <div className={styles.bigEmpty}>
      <div className={cx(styles.bigEmptyIco, ico)}><Icon icon={icon} size={30} /></div>
      <div>
        <h3 className={styles.h}>{title}</h3>
        <p className={styles.p}>{description}</p>
      </div>
      {actions}
    </div>
  )
}

/** Cross-cutting empty/error state reference. */
export function Estados() {
  return (
    <div className={styles.root}>
      <div style={{ marginBottom: 34 }}>
        <span className="eyebrow">Referencia transversal</span>
        <h1 className="h1" style={{ marginTop: 16, fontSize: 38 }}>Estados vacíos y de error</h1>
        <p className="muted" style={{ maxWidth: 600, marginTop: 12 }}>
          Patrones reutilizables en toda la app. Todos con lenguaje claro para un usuario no técnico, un ícono con intención y una salida — nunca un callejón sin salida.
        </p>
      </div>

      <div className={styles.grid}>
        <Cell tone="var(--fg-3)" label="Sin datasets">
          <BigEmpty
            icon={Tray}
            ico={styles.icoNeutral}
            title="Todavía no tenés datos"
            description="Subí tu primer archivo CSV, Excel o PDF y empezá a hacerle preguntas a tus datos."
            actions={<Link className="btn btn-primary" to="/ingesta"><Icon icon={UploadSimple} size="sm" />Subir mi primer archivo</Link>}
          />
        </Cell>

        <Cell tone="var(--danger)" label="Error de red">
          <BigEmpty
            icon={WifiSlash}
            ico={styles.icoErr}
            title="Se cortó la conexión"
            description="No pudimos comunicarnos con el servidor. Revisá tu conexión y probá de nuevo."
            actions={<Button variant="glass"><Icon icon={ArrowClockwise} size="sm" />Reintentar</Button>}
          />
        </Cell>

        <Cell tone="var(--warn)" label="Protección SQL (solo lectura)">
          <div className={styles.guard}>
            <Icon icon={ShieldWarning} weight="fill" size={22} className={styles.guardIco} />
            <div>
              <div style={{ fontWeight: 600, fontSize: 13.5, color: 'var(--fg-1)' }}>Solo puedo leer tus datos</div>
              <p style={{ margin: '6px 0 0', color: 'var(--fg-2)', fontSize: 13, lineHeight: 1.5 }}>Solo ejecuto consultas de lectura, así que nunca modifico ni borro tu información. Probá reformularlo como una pregunta.</p>
              <Button variant="glass" size="sm" style={{ marginTop: 12 }}><Icon icon={PencilSimple} size="sm" />Reformular la pregunta</Button>
            </div>
          </div>
        </Cell>

        <Cell tone="var(--warn)" label="Sesión vencida">
          <BigEmpty
            icon={ClockUser}
            ico={styles.icoWarn}
            title="Tu sesión venció"
            description="Por seguridad cerramos tu sesión tras un tiempo de inactividad. Iniciá sesión de nuevo para continuar."
            actions={<Link className="btn btn-primary" to="/login"><Icon icon={SignIn} size="sm" />Volver a iniciar sesión</Link>}
          />
        </Cell>

        <Cell tone="var(--accent)" label="Plan agotado">
          <BigEmpty
            icon={Lightning}
            ico={styles.icoAccent}
            title="Llegaste al límite del plan Free"
            description="Usaste tus 20 consultas de este mes. Pasá a Pro para tener 250 consultas mensuales y más."
            actions={
              <div className="row" style={{ gap: 10 }}>
                <Link className="btn btn-glass" to="/app">Esperar al reinicio</Link>
                <Link className="btn btn-primary" to="/suscripcion"><Icon icon={Lightning} size="sm" />Pasar a Pro</Link>
              </div>
            }
          />
        </Cell>

        <Cell tone="var(--fg-3)" label="Sin resultados">
          <BigEmpty
            icon={MagnifyingGlass}
            ico={styles.icoNeutral}
            title="Tu consulta no devolvió filas"
            description="Ningún dato coincide con esa pregunta. Probá ampliar el período o quitar un filtro."
            actions={<Button variant="glass" size="sm"><Icon icon={ArrowCounterClockwise} size="sm" />Ajustar la pregunta</Button>}
          />
        </Cell>
      </div>
    </div>
  )
}
