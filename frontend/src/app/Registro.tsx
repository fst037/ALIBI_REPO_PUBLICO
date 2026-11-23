import { useEffect, useState } from 'react'
import type { ChangeEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ArrowRight, Check, CheckCircle, Circle, EnvelopeSimple, Lock, LockSimple, WarningCircle } from '@phosphor-icons/react'
import { AlibiMark, Avatar, Button, CodeInput, Field, Icon, Segmented, Spinner } from '../components/ui'
import * as api from '../lib/api'
import { ApiError } from '../lib/api/http'
import { isAuthenticated } from '../lib/api/session'
import type { BillingCycle, Plan, PlanId } from '../types'
import { cx } from '../lib/cx'
import { formatArs } from '../lib/format'
import { TERMS_VERSION } from '../lib/legal'
import { TermsConsent } from '../features/legal/TermsConsent'
import { priceRuleNote } from '../features/billing/priceNote'
import { useResendCooldown } from '../features/auth/useResendCooldown'
import { GoogleButton } from '../features/auth/GoogleButton'
import { googleEnabled } from '../features/auth/googleIdentity'
import { PASSWORD_RULES, passwordMeetsRequirements, passwordStrength, validEmail } from '../lib/validation'
import auth from './auth.module.css'
import billing from './Suscripcion.module.css'
import styles from './Registro.module.css'

type Step = 'form' | 'verify' | 'google-terms' | 'plan' | 'payment'

const PW_COLORS = ['var(--danger)', 'var(--warn)', 'var(--accent-dim)', 'var(--accent)']

const BILLING_OPTIONS: { value: BillingCycle; label: string }[] = [
  { value: 'monthly', label: 'Mensual' },
  { value: 'annual', label: 'Anual · −20%' },
]

export function Registro() {
  const navigate = useNavigate()
  const location = useLocation()
  // Login sends a brand-new Google account here to choose its plan. Honoured
  // only with a session: the plan step charges the signed-in account.
  const resumeAtPlan = (location.state as { step?: Step } | null)?.step === 'plan' && isAuthenticated()
  const [step, setStep] = useState<Step>(resumeAtPlan ? 'plan' : 'form')
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [email, setEmail] = useState('')
  const [pw, setPw] = useState('')
  const [emailErr, setEmailErr] = useState(false)
  const [nameErr, setNameErr] = useState(false)
  // Express consent to the Terms and the Privacy Policy, including the
  // international transfer of data (Ley 25.326). Never pre-ticked.
  const [accepted, setAccepted] = useState(false)
  // A Google account with no ALIBI account yet, held while the user accepts
  // the Terms (`google-terms`) — only when they hadn't ticked the box above.
  const [googleCredential, setGoogleCredential] = useState<string | null>(null)
  const [code, setCode] = useState<string[]>(['', '', '', '', '', ''])
  const [formError, setFormError] = useState('')
  const [codeMsg, setCodeMsg] = useState('')
  const [notice, setNotice] = useState('')
  const cooldown = useResendCooldown()
  const [billingCycle, setBillingCycle] = useState<BillingCycle>('annual')
  const [plan, setPlan] = useState<PlanId>('free')
  // The catalogue, from the API (public endpoint) — prices are the backend's, in ARS.
  const [plans, setPlans] = useState<Plan[]>([])
  const [busy, setBusy] = useState(false)
  const [payErr, setPayErr] = useState(false)

  const annual = billingCycle === 'annual'
  const score = passwordStrength(pw)
  const pwOk = passwordMeetsRequirements(pw)
  // Both halves, not one "full name" box: the account screen and the greeting
  // read them separately, and splitting a single field on the first space gets
  // compound surnames wrong.
  const fullName = `${firstName.trim()} ${lastName.trim()}`.trim()
  const nameOk = firstName.trim().length > 0 && lastName.trim().length > 0
  const formDisabled = !(nameOk && validEmail(email) && pwOk && accepted)
  const codeDisabled = code.some((c) => !c)

  const onEmail = (e: ChangeEvent<HTMLInputElement>) => {
    setEmail(e.target.value)
    setEmailErr(false)
  }
  const onEmailBlur = () => {
    if (email && !validEmail(email)) setEmailErr(true)
  }

  // Registration actions flow through lib/api — the seam swapped for the real backend.
  const submitForm = async () => {
    if (!nameOk) {
      setNameErr(true)
      return
    }
    if (!validEmail(email)) {
      setEmailErr(true)
      return
    }
    if (!pwOk || !accepted || busy) return
    setEmailErr(false)
    setNameErr(false)
    setFormError('')
    setBusy(true)
    try {
      // Emails the code; the session opens at verify — see `lib/api/auth.register`.
      await api.register({ email, password: pw, name: fullName, acceptedTermsVersion: TERMS_VERSION })
      setCode(['', '', '', '', '', ''])
      setCodeMsg('')
      setNotice('')
      cooldown.start()
      setStep('verify')
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'No pudimos crear la cuenta. Probá de nuevo.')
    } finally {
      setBusy(false)
    }
  }
  const onVerify = async () => {
    if (codeDisabled || busy) return
    setBusy(true)
    setCodeMsg('')
    setNotice('')
    try {
      await api.verifyCode(code.join(''))
      setStep('plan')
    } catch (err) {
      setCode(['', '', '', '', '', ''])
      setCodeMsg(err instanceof ApiError ? err.message : 'No pudimos verificar el código. Probá de nuevo.')
    } finally {
      setBusy(false)
    }
  }
  const onResend = async () => {
    if (cooldown.left > 0) return
    setCodeMsg('')
    setNotice('')
    try {
      await api.resendCode()
      setCode(['', '', '', '', '', ''])
      setNotice('Te enviamos un código nuevo.')
      cooldown.start()
    } catch (err) {
      setCodeMsg(err instanceof ApiError ? err.message : 'No pudimos reenviar el código.')
    }
  }
  // "Continuar con Google": no code step — Google already verified the address.
  // An account that already existed is just signed in, and goes to the app.
  const afterGoogle = (outcome: 'authenticated' | 'created') => {
    if (outcome === 'created') setStep('plan')
    else navigate('/app')
  }
  const onGoogle = async (credential: string) => {
    setFormError('')
    setBusy(true)
    try {
      afterGoogle(
        await api.signInWithGoogle(credential, { acceptedTermsVersion: accepted ? TERMS_VERSION : undefined }),
      )
    } catch (err) {
      if (err instanceof ApiError && err.code === 'terms_required') {
        setGoogleCredential(credential)
        setStep('google-terms')
      } else {
        setFormError(err instanceof ApiError ? err.message : 'No pudimos ingresar con Google. Probá de nuevo.')
      }
    } finally {
      setBusy(false)
    }
  }
  const onGoogleTerms = async () => {
    if (!googleCredential || !accepted || busy) return
    setFormError('')
    setBusy(true)
    try {
      afterGoogle(await api.signInWithGoogle(googleCredential, { acceptedTermsVersion: TERMS_VERSION }))
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'No pudimos crear la cuenta. Probá de nuevo.')
    } finally {
      setBusy(false)
    }
  }
  const planId: PlanId = plan

  useEffect(() => {
    let alive = true
    void api.listPlans().then((p) => alive && setPlans(p))
    return () => {
      alive = false
    }
  }, [])

  const onChoosePlan = async () => {
    if (busy) return
    // Free needs no payment — activate and go to onboarding. Paid plans go to
    // the payment step (flow 0.2, step 3) before the plan is charged.
    if (plan !== 'free') {
      setPayErr(false)
      setStep('payment')
      return
    }
    setBusy(true)
    try {
      await api.upgradePlan(planId, billingCycle)
      navigate('/onboarding')
    } finally {
      setBusy(false)
    }
  }
  const payWithMercadoPago = async () => {
    if (busy) return
    setBusy(true)
    setPayErr(false)
    try {
      // The card is entered on Mercado Pago's page, never here. It sends the
      // user back to /pago/resultado, which verifies the payment.
      const { checkoutUrl } = await api.startCheckout(planId, billingCycle, 'registro')
      window.location.assign(checkoutUrl)
    } catch {
      setPayErr(true)
      setBusy(false)
    }
  }

  const selectedPlan = plans.find((p) => p.id === plan)
  const monthlyRate = selectedPlan ? api.priceFor(selectedPlan, billingCycle) : 0
  const amount = annual ? monthlyRate * 12 : monthlyRate
  const tax = Math.round(amount * 0.21)
  const total = amount + tax
  const proPlan = plans.find((p) => p.id === 'pro')
  const planCta = plan === 'free' ? 'Empezar gratis' : 'Continuar al pago'
  const planFootNote =
    plan === 'free' ? 'No hace falta tarjeta. Pasá a Pro cuando quieras.' : 'Vas a cargar tu tarjeta en el próximo paso.'

  return (
    <div className={auth.auth}>
      <aside className={auth.authBrand}>
        <Link className={auth.abTop} to="/">
          <AlibiMark size={30} />ALIBI
        </Link>
        <div className={auth.abMid}>
          <div className={auth.abQuote}>
            “Dejé de esperar los reportes. Ahora le pregunto a ALIBI y tengo el gráfico en segundos.”
          </div>
          <div className={auth.abBy}>
            <Avatar name="Marina G" />
            <div>
              <div style={{ color: 'var(--fg-1)', fontWeight: 500 }}>Marina G.</div>
              <div>Analista comercial</div>
            </div>
          </div>
        </div>
        <div className={auth.abFeats}>
          <div className={auth.abFeat}><Icon icon={Check} color="var(--accent)" />Sin SQL, sin fórmulas</div>
          <div className={auth.abFeat}><Icon icon={Check} color="var(--accent)" />Tus datos, solo lectura</div>
          <div className={auth.abFeat}><Icon icon={Check} color="var(--accent)" />Empezá gratis, sin tarjeta</div>
        </div>
      </aside>

      <main className={auth.authMain}>
        <div className={auth.authCard} style={step === 'plan' ? { maxWidth: 840 } : step === 'payment' ? { maxWidth: 760 } : undefined}>
          <div className={styles.stepsInd}>
            <div className={cx(styles.stepDot, styles.done)} />
            <div className={cx(styles.stepDot, step !== 'form' && step !== 'google-terms' && styles.done)} />
            <div className={cx(styles.stepDot, (step === 'plan' || step === 'payment') && styles.done)} />
          </div>

          {step === 'form' && (
            <div className="fade-up">
              <h1 className={auth.authH}>Creá tu cuenta</h1>
              <p className={auth.authSub}>Empezá gratis. Tu primera pregunta a tus datos en menos de dos minutos.</p>
              {googleEnabled && (
                <>
                  <GoogleButton onCredential={onGoogle} context="signup" />
                  <div className={auth.dividerOr}>o con tu email</div>
                </>
              )}
              <div className={auth.formFields}>
                <div className={styles.nameRow}>
                  <Field
                    label="Nombre"
                    placeholder="Tu nombre"
                    value={firstName}
                    onChange={(e) => { setFirstName(e.target.value); setNameErr(false) }}
                    error={nameErr && !firstName.trim() ? 'Obligatorio' : undefined}
                  />
                  <Field
                    label="Apellido"
                    placeholder="Tu apellido"
                    value={lastName}
                    onChange={(e) => { setLastName(e.target.value); setNameErr(false) }}
                    error={nameErr && !lastName.trim() ? 'Obligatorio' : undefined}
                  />
                </div>
                <Field
                  label="Email"
                  placeholder="vos@empresa.com"
                  value={email}
                  onChange={onEmail}
                  onBlur={onEmailBlur}
                  error={emailErr ? 'Ingresá un email válido' : undefined}
                />
                <div>
                  <Field
                    label="Contraseña"
                    icon={Lock}
                    type="password"
                    placeholder="Al menos 8 caracteres"
                    value={pw}
                    onChange={(e) => setPw(e.target.value)}
                  />
                  <div className={styles.pwMeter}>
                    {[0, 1, 2, 3].map((i) => (
                      <div
                        key={i}
                        className={styles.pwSeg}
                        style={{ background: i < score ? PW_COLORS[Math.min(score - 1, 3)] : 'var(--glass-border)' }}
                      />
                    ))}
                  </div>
                  <ul className={styles.pwReqs}>
                    {PASSWORD_RULES.map((rule) => {
                      const met = rule.test(pw)
                      return (
                        <li key={rule.key} className={cx(styles.pwReq, met && styles.met)}>
                          <Icon
                            icon={met ? CheckCircle : Circle}
                            size="sm"
                            weight={met ? 'fill' : 'regular'}
                            color={met ? 'var(--accent)' : 'var(--fg-4)'}
                          />
                          {rule.label}
                        </li>
                      )
                    })}
                  </ul>
                </div>
              </div>
              <TermsConsent checked={accepted} onChange={setAccepted} />
              <Button
                variant="primary"
                disabled={formDisabled || busy}
                onClick={submitForm}
                style={{ width: '100%', justifyContent: 'center', marginTop: 22 }}
              >
                {busy ? <Spinner /> : <>Crear cuenta<Icon icon={ArrowRight} size="sm" /></>}
              </Button>
              {formError && (
                <div className="field-msg" style={{ justifyContent: 'center', marginTop: 10 }}>{formError}</div>
              )}
              <div className={styles.legal}>
                Tus datos se consultan solo en modo lectura, no se ceden a terceros y no se usan para entrenar modelos.
              </div>
              <div className={auth.authFoot}>
                ¿Ya tenés cuenta? <Link to="/login">Iniciá sesión</Link>
              </div>
            </div>
          )}

          {step === 'verify' && (
            <div className="fade-up" style={{ textAlign: 'center' }}>
              <div className={auth.mailIco}><Icon icon={EnvelopeSimple} size={26} /></div>
              <h1 className={auth.authH}>Verificá tu email</h1>
              <p className={auth.authSub}>
                Enviamos un código de 6 dígitos a<br />
                <span style={{ color: 'var(--fg-1)', fontWeight: 500 }}>{email || 'tu email'}</span>
              </p>
              <CodeInput value={code} onChange={setCode} aria-label="Código de verificación" />
              <Button
                variant="primary"
                disabled={codeDisabled || busy}
                onClick={onVerify}
                style={{ width: '100%', justifyContent: 'center', marginTop: 24 }}
              >
                {busy ? <Spinner /> : <>Verificar y continuar<Icon icon={ArrowRight} size="sm" /></>}
              </Button>
              {codeMsg && (
                <div className="field-msg" style={{ justifyContent: 'center', marginTop: 10 }}>{codeMsg}</div>
              )}
{notice && (
                <div className="field-msg" style={{ justifyContent: 'center', marginTop: 10, color: 'var(--accent)' }}>{notice}</div>
              )}
              <div className={auth.resend}>
                ¿No te llegó?{' '}
                {cooldown.left > 0 ? (
                  <span>Podés pedir otro en {cooldown.label}</span>
                ) : (
                  <button type="button" onClick={onResend} style={{ color: 'var(--accent)', fontWeight: 500, background: 'none', border: 'none', cursor: 'pointer', font: 'inherit' }}>
                    Reenviar código
                  </button>
                )}
              </div>
              <div className={auth.authFoot}>
                <button type="button" onClick={() => setStep('form')} style={{ color: 'var(--accent)', fontWeight: 500, background: 'none', border: 'none', cursor: 'pointer', font: 'inherit' }}>
                  ← Cambiar email
                </button>
              </div>
            </div>
          )}

          {step === 'google-terms' && (
            <div className="fade-up">
              <h1 className={auth.authH}>Un paso más</h1>
              <p className={auth.authSub}>Para crear tu cuenta con Google, aceptá los términos.</p>
              <TermsConsent checked={accepted} onChange={setAccepted} />
              <Button
                variant="primary"
                disabled={!accepted || busy}
                onClick={onGoogleTerms}
                style={{ width: '100%', justifyContent: 'center', marginTop: 22 }}
              >
                {busy ? <Spinner /> : <>Crear cuenta<Icon icon={ArrowRight} size="sm" /></>}
              </Button>
              {formError && (
                <div className="field-msg" style={{ justifyContent: 'center', marginTop: 10 }}>{formError}</div>
              )}
              <div className={auth.authFoot}>
                <button
                  type="button"
                  onClick={() => { setGoogleCredential(null); setFormError(''); setStep('form') }}
                  style={{ color: 'var(--accent)', fontWeight: 500, background: 'none', border: 'none', cursor: 'pointer', font: 'inherit' }}
                >
                  ← Volver
                </button>
              </div>
            </div>
          )}

          {step === 'plan' && (
            <div className="fade-up" style={{ textAlign: 'center' }}>
              <h1 className={auth.authH}>Elegí tu plan</h1>
              <p className={auth.authSub}>Podés empezar gratis y cambiar de plan cuando quieras.</p>
              <div className="center">
                <Segmented options={BILLING_OPTIONS} value={billingCycle} onChange={setBillingCycle} aria-label="Ciclo de facturación" />
              </div>
              <div className={styles.planPick}>
                {plans.length === 0 && <div className="center" style={{ gridColumn: '1 / -1', padding: 24 }}><Spinner /></div>}
                {plans.map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    className={cx(styles.pp, plan === opt.id && styles.sel)}
                    onClick={() => setPlan(opt.id)}
                  >
                    <div className="between">
                      <span className="plan-name" style={opt.recommended ? { color: 'var(--accent)' } : undefined}>{opt.name.toUpperCase()}</span>
                      <span className={styles.ppRadio} />
                    </div>
                    <div className={styles.ppPrice}>
                      <span className={styles.ppAmt}>{formatArs(api.priceFor(opt, billingCycle))}</span>
                      <span className={styles.ppPer}>{opt.priceMonthly > 0 ? '/mes + IVA' : '/mes'}</span>
                    </div>
                    <ul className={styles.ppList}>
                      {opt.features.map((f) => (
                        <li key={f}><Icon icon={Check} size="sm" color="var(--accent)" />{f}</li>
                      ))}
                    </ul>
                  </button>
                ))}
              </div>
              <Button
                variant="primary"
                onClick={onChoosePlan}
                disabled={busy}
                style={{ width: '100%', justifyContent: 'center', marginTop: 22 }}
              >
                {busy ? <Spinner /> : <>{planCta}<Icon icon={ArrowRight} size="sm" /></>}
              </Button>
              <div className={auth.authFoot}>{planFootNote}</div>
              {proPlan && <div className={styles.legal}>{priceRuleNote(proPlan)}</div>}
            </div>
          )}

          {step === 'payment' && (
            <div className="fade-up">
              <button
                type="button"
                onClick={() => setStep('plan')}
                style={{ color: 'var(--accent)', fontWeight: 500, background: 'none', border: 'none', cursor: 'pointer', font: 'inherit', marginBottom: 14 }}
              >
                ← Volver a los planes
              </button>
              <h1 className={auth.authH}>Datos de pago</h1>
              <p className={auth.authSub}>
                Estás pasando a <span style={{ color: 'var(--fg-1)', fontWeight: 500 }}>{selectedPlan?.name}</span>. Cancelás cuando quieras.
              </p>

              <div className={styles.payGrid}>
                <div>
                  <div className="hint" style={{ display: 'flex', alignItems: 'flex-start', gap: 7 }}>
                    <Icon icon={LockSimple} size="sm" />
                    Vas a completar el pago en la página segura de Mercado Pago. ALIBI nunca ve ni guarda los datos de tu tarjeta.
                  </div>
                  <div className="hint" style={{ marginTop: 14 }}>
                    Mientras usemos credenciales de prueba, el pago es una simulación: usá una tarjeta de prueba de Mercado Pago y no se cobra dinero real.
                  </div>
                  {payErr && (
                    <div className="field-msg" style={{ marginTop: 14 }}>
                      <Icon icon={WarningCircle} size="sm" />No pudimos iniciar el pago. Probá de nuevo en unos minutos.
                    </div>
                  )}
                </div>

                <div className={styles.paySummary}>
                  <div className="sb-lab" style={{ padding: '0 0 14px' }}>Resumen</div>
                  <div className={billing.sumLine}><span>{selectedPlan?.name} ({annual ? '12 meses' : 'mensual'})</span><span className="mono">{formatArs(amount)}</span></div>
                  {annual && <div className={billing.sumLine} style={{ color: 'var(--accent)' }}><span>Incluye descuento anual</span><span className="mono">−20%</span></div>}
                  <div className={billing.sumLine}><span>IVA (21%)</span><span className="mono">{formatArs(tax)}</span></div>
                  <div className={billing.sumTotal}><span>Total {annual ? 'anual' : 'mensual'}</span><span className="mono">{formatArs(total)}</span></div>
                  <div className="grow" />
                  <Button
                    variant="primary"
                    onClick={payWithMercadoPago}
                    disabled={busy}
                    style={{ justifyContent: 'center', marginTop: 20, width: '100%' }}
                  >
                    {busy ? <Spinner /> : <><Icon icon={LockSimple} size="sm" />Pagar con Mercado Pago</>}
                  </Button>
                  <div className="hint" style={{ textAlign: 'center', marginTop: 10 }}>
                    {annual ? 'Se cobra por adelantado y mantiene el precio 12 meses.' : 'Se renueva automáticamente. Cancelás cuando quieras.'}{' '}
                    Factura electrónica.
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
