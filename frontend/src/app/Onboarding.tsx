import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowRight,
  ChartBar,
  ChatsCircle,
  Database,
  RocketLaunch,
  UploadSimple,
} from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { Button, Eyebrow, Icon } from '../components/ui'
import { useAuth } from '../features/auth/AuthContext'
import { cx } from '../lib/cx'
import styles from './Onboarding.module.css'

interface Slide {
  icon: PhosphorIcon
  mod: string
  h: string
  p: string
}
const SLIDES: Slide[] = [
  {
    icon: Database,
    mod: 'Paso 1 · ETL liviano',
    h: 'Conectá tus datos',
    p: 'Soltá un CSV, Excel o PDF. ALIBI detecta las columnas y sus tipos automáticamente — vos solo confirmás.',
  },
  {
    icon: ChatsCircle,
    mod: 'Paso 2 · Text-to-SQL',
    h: 'Preguntá en lenguaje natural',
    p: 'Escribí lo que querés saber como se lo dirías a un colega. Convertimos tu pregunta en una consulta segura.',
  },
  {
    icon: ChartBar,
    mod: 'Paso 3 · Auto-visualización',
    h: 'Gráficos automáticos',
    p: 'Cada respuesta viene con una tabla y el gráfico que mejor la cuenta.',
  },
]

export function Onboarding() {
  const [i, setI] = useState(0)
  // The real signed-in user, not a hard-coded name. Registration now opens the
  // session, so by the time anyone reaches this screen there is a user to greet.
  const { user } = useAuth()
  const firstName = user?.name.trim().split(' ')[0] ?? ''
  const onSlides = i < SLIDES.length
  const slide = SLIDES[Math.min(i, SLIDES.length - 1)]
  const nextLabel = i === SLIDES.length - 1 ? 'Empezar' : 'Siguiente'

  return (
    <div className={styles.ob}>
      <Link className={styles.obTop} to="/">
        <span className={styles.obMk}>A</span>ALIBI
      </Link>
      <Link className={cx('btn', 'btn-ghost', 'btn-sm', styles.obSkip)} to="/app">
        Omitir<Icon icon={ArrowRight} size="sm" />
      </Link>

      <div className={styles.obCard}>
        {onSlides && (
          <div className="fade-up" key={i}>
            <div className={styles.obVisual}>
              <Icon icon={slide.icon} size={74} className={styles.obBigIco} />
            </div>
            <div className={styles.obMod}><Eyebrow>{slide.mod}</Eyebrow></div>
            <h1 className={styles.obH}>{slide.h}</h1>
            <p className={styles.obP}>{slide.p}</p>
          </div>
        )}

        {!onSlides && (
          <div className="fade-up">
            <div className={styles.obVisual}>
              <Icon icon={RocketLaunch} size={74} className={styles.obBigIco} />
            </div>
            <h1 className={styles.obH}>{firstName ? `Todo listo, ${firstName}` : 'Todo listo'}</h1>
            <p className={styles.obP}>
              ¿Por dónde querés empezar? Subí tu primer archivo o entrá directo a la app.
            </p>
            <div className={styles.obStart} style={{ marginTop: 28 }}>
              <Link className={styles.startOpt} to="/ingesta">
                <div className={styles.startIco}><Icon icon={UploadSimple} size={21} /></div>
                <h3>Subir mi primer archivo</h3>
                <p>CSV, Excel o PDF. Inferimos el esquema por vos.</p>
              </Link>
              {/* No sample dataset: there is none to offer. Every answer comes
                  from a real query over the user's own files. */}
              <Link className={styles.startOpt} to="/app">
                <div className={styles.startIco}><Icon icon={ChatsCircle} size={21} /></div>
                <h3>Ir a la app</h3>
                <p>Empezá desde el espacio de trabajo y elegí una carpeta cuando quieras.</p>
              </Link>
            </div>
          </div>
        )}

        <div className={styles.obDots}>
          {[0, 1, 2, 3].map((d) => (
            <button
              key={d}
              type="button"
              className={cx(styles.obDot, d === i && styles.on)}
              aria-label={`Ir al paso ${d + 1}`}
              onClick={() => setI(d)}
            />
          ))}
        </div>

        {onSlides && (
          <div className={styles.obNav}>
            {i > 0 && (
              <Button variant="glass" onClick={() => setI((v) => Math.max(0, v - 1))}>
                <Icon icon={ArrowLeft} size="sm" />Volver
              </Button>
            )}
            <Button variant="primary" onClick={() => setI((v) => Math.min(3, v + 1))}>
              {nextLabel}<Icon icon={ArrowRight} size="sm" />
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
