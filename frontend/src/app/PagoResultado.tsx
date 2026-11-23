import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, CheckFat, Clock, WarningCircle, XCircle } from '@phosphor-icons/react'
import { Button, Icon, Spinner } from '../components/ui'
import * as api from '../lib/api'
import { ApiError } from '../lib/api/http'
import type { CheckoutOrigin } from '../lib/api'
import type { Subscription } from '../types'
import auth from './auth.module.css'
import billing from './Suscripcion.module.css'

type Outcome =
  | { kind: 'confirming' }
  | { kind: 'ok'; subscription: Subscription }
  | { kind: 'pending' }
  | { kind: 'rejected'; message: string }
  | { kind: 'cancelled' }
  | { kind: 'error'; message: string }

/**
 * Where Mercado Pago sends the user back after paying (`/pago/resultado`).
 *
 * The query string is not believed: it only carries the `payment_id`, which the
 * backend looks up at Mercado Pago before it moves the plan — a `status=approved`
 * typed into the address bar changes nothing.
 */
export function PagoResultado() {
  const [params] = useSearchParams()
  const paymentId = params.get('payment_id') ?? params.get('collection_id')
  const [outcome, setOutcome] = useState<Outcome>(paymentId ? { kind: 'confirming' } : { kind: 'cancelled' })
  // Confirming is idempotent, but there is no reason to ask twice (StrictMode runs effects twice in dev).
  const asked = useRef(false)
  const from = useRef<CheckoutOrigin>('cuenta')

  useEffect(() => {
    if (!paymentId || asked.current) return
    asked.current = true
    from.current = api.takeCheckoutOrigin()
    api
      .confirmPayment(paymentId)
      .then((subscription) => setOutcome({ kind: 'ok', subscription }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.code === 'payment_pending') setOutcome({ kind: 'pending' })
        else if (err instanceof ApiError && (err.code === 'payment_rejected' || err.code === 'payment_not_found'))
          setOutcome({ kind: 'rejected', message: err.message })
        else
          setOutcome({
            kind: 'error',
            message: err instanceof ApiError ? err.message : 'No pudimos verificar tu pago. Probá de nuevo.',
          })
      })
  }, [paymentId])

  const next = from.current === 'registro' ? '/onboarding' : '/cuenta?tab=billing'
  const retry = from.current === 'registro' ? '/registro' : '/cuenta?tab=billing'

  return (
    <div className={auth.auth}>
      <main className={auth.authMain}>
        <div className={auth.authCard}>
          <div className="fade-up" style={{ textAlign: 'center' }}>
            {outcome.kind === 'confirming' && (
              <>
                <Spinner />
                <h1 className={auth.authH} style={{ marginTop: 18 }}>Verificando tu pago…</h1>
                <p className={auth.authSub}>Un momento, estamos confirmando con Mercado Pago.</p>
              </>
            )}

            {outcome.kind === 'ok' && (
              <>
                <div className={billing.doneRing}><Icon icon={CheckFat} size={30} /></div>
                <h1 className={auth.authH}>¡Pago confirmado!</h1>
                <p className={auth.authSub}>Tus nuevos límites ya están activos.</p>
                <Link className="btn btn-primary" to={next} style={{ width: '100%', justifyContent: 'center' }}>
                  {from.current === 'registro' ? 'Empezar a usar ALIBI' : 'Ir a mi cuenta'}
                  <Icon icon={ArrowRight} size="sm" />
                </Link>
              </>
            )}

            {outcome.kind === 'pending' && (
              <>
                <div className={billing.doneRing}><Icon icon={Clock} size={30} /></div>
                <h1 className={auth.authH}>Tu pago se está procesando</h1>
                <p className={auth.authSub}>
                  Mercado Pago todavía no lo acreditó. Cuando se apruebe, volvé acá y tu plan se activa.
                </p>
                <Button variant="primary" onClick={() => window.location.reload()} style={{ width: '100%', justifyContent: 'center' }}>
                  Revisar de nuevo
                </Button>
              </>
            )}

            {(outcome.kind === 'rejected' || outcome.kind === 'cancelled') && (
              <>
                <div className={billing.doneRing}><Icon icon={XCircle} size={30} /></div>
                <h1 className={auth.authH}>No se completó el pago</h1>
                <p className={auth.authSub}>
                  {outcome.kind === 'rejected' ? outcome.message : 'No se te cobró nada. Podés intentarlo de nuevo cuando quieras.'}
                </p>
                <Link className="btn btn-primary" to={retry} style={{ width: '100%', justifyContent: 'center' }}>
                  Volver
                </Link>
              </>
            )}

            {outcome.kind === 'error' && (
              <>
                <div className={billing.doneRing}><Icon icon={WarningCircle} size={30} /></div>
                <h1 className={auth.authH}>No pudimos verificar tu pago</h1>
                <p className={auth.authSub}>{outcome.message}</p>
                <Button variant="primary" onClick={() => window.location.reload()} style={{ width: '100%', justifyContent: 'center' }}>
                  Reintentar
                </Button>
              </>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
