import { useSyncExternalStore } from 'react'
import { dismissDbWake, getDbWakeState, subscribeDbWake } from '../../lib/api'
import { Button, Spinner, Toast } from '../../components/ui'
import { cx } from '../../lib/cx'
import styles from './DbWakeNotice.module.css'

/**
 * App-wide notice while the database starts after an idle stop (503
 * `db_waking`, see `lib/api/dbWake.ts`). Once it answers, offers a reload
 * rather than doing one: the screen behind may hold a half-typed form.
 */
export function DbWakeNotice() {
  const state = useSyncExternalStore(subscribeDbWake, getDbWakeState)
  if (state === 'idle') return null

  return (
    <div className={cx('fade-up', styles.notice)} aria-live="polite">
      {state === 'waking' && (
        <Toast tone="info" title="Despertando la base de datos">
          Se apagÃ³ por inactividad para ahorrar costos. Vuelve en unos minutos; podÃ©s dejar esta pestaÃ±a abierta.{' '}
          <Spinner />
        </Toast>
      )}
      {state === 'no_capacity' && (
        <Toast tone="err" title="AWS no tiene capacidad para iniciar la base de datos">
          Se apagó por inactividad y AWS no tiene lugar para volver a encenderla en este momento. Seguimos
          reintentando; puede tardar. <Spinner />
        </Toast>
      )}
      {state === 'ready' && (
        <Toast tone="ok" title="La base de datos estÃ¡ lista">
          RecargÃ¡ para seguir donde estabas.
          <span className={styles.actions}>
            <Button size="sm" onClick={() => window.location.reload()}>
              Recargar
            </Button>
            <Button size="sm" variant="ghost" onClick={dismissDbWake}>
              Cerrar
            </Button>
          </span>
        </Toast>
      )}
      {state === 'failed' && (
        <Toast tone="err" title="La base de datos no respondiÃ³">
          ProbÃ¡ recargar en unos minutos.
          <span className={styles.actions}>
            <Button size="sm" variant="ghost" onClick={dismissDbWake}>
              Cerrar
            </Button>
          </span>
        </Toast>
      )}
    </div>
  )
}
