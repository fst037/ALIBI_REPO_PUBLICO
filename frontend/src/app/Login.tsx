import { useState } from 'react'
import type { ChangeEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  ChatsCircle,
  Check,
  EnvelopeSimple,
  Lightning,
  Lock,
  PaperPlaneTilt,
  ShieldCheck,
} from '@phosphor-icons/react'
import { AlibiMark, Button, CodeInput, Field, Icon, Spinner } from '../components/ui'
import * as api from '../lib/api'
import { ApiError } from '../lib/api/http'
import { cx } from '../lib/cx'
import { useResendCooldown } from '../features/auth/useResendCooldown'
import { GoogleButton } from '../features/auth/GoogleButton'
import { googleEnabled } from '../features/auth/googleIdentity'
import { TermsConsent } from '../features/legal/TermsConsent'
import { TERMS_VERSION } from '../lib/legal'
import { validEmail } from '../lib/validation'
import auth from './auth.module.css'
import styles from './Login.module.css'

type View = 'login' | 'verify' | 'google-terms' | 'forgot' | 'forgot-sent'

export function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/app'
  const [view, setView] = useState<View>('login')
  const [email, setEmail] = useState('')
  const [pw, setPw] = useState('')
  const [remember, setRemember] = useState(true)
  const [code, setCode] = useState<string[]>(['', '', '', '', '', ''])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const cooldown = useResendCooldown()
  const [emailErr, setEmailErr] = useState(false)
  // The ID token of a Google account with no ALIBI account yet, held while the
  // user reads and accepts the Terms (`google-terms`). Valid for an hour.
  const [googleCredential, setGoogleCredential] = useState<string | null>(null)
  const [accepted, setAccepted] = useState(false)

  const emailOr = email || 'tu email'
  const loginDisabled = !(validEmail(email) && pw.length > 0)
  const forgotDisabled = !validEmail(email)
  const codeDisabled = code.some((c) => !c)

  const onEmail = (e: ChangeEvent<HTMLInputElement>) => {
    setEmail(e.target.value)
    setError('')
    setEmailErr(false)
  }
  const onEmailBlur = () => {
    if (email && !validEmail(email)) setEmailErr(true)
  }
  const onPw = (e: ChangeEvent<HTMLInputElement>) => setPw(e.target.value)
  const back = () => {
    setView('login')
    setCode(['', '', '', '', '', ''])
    setError('')
    setEmailErr(false)
    setGoogleCredential(null)
  }

  // Auth actions flow through lib/api — the single seam swapped for the real backend.
  const onLogin = async () => {
    if (loginDisabled || busy) return
    setBusy(true)
    setError('')
    try {
      // A verified account is signed in here; an unverified one (registration
      // abandoned before the code) gets a fresh code by email first.
      if ((await api.login(email, pw, remember)) === 'authenticated') {
        navigate(from, { replace: true })
      } else {
        setNotice('')
        cooldown.start()
        setView('verify')
      }
    } catch (err) {
      setError(
        err instanceof ApiError && err.code !== 'invalid_credentials'
          ? err.message
          : 'Email o contraseña incorrectos. Probá de nuevo.',
      )
    } finally {
      setBusy(false)
    }
  }
  const onVerify = async () => {
    if (codeDisabled || busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await api.verifyCode(code.join(''))
      navigate(from, { replace: true })
    } catch (err) {
      setCode(['', '', '', '', '', ''])
      setError(err instanceof ApiError ? err.message : 'No pudimos verificar el código. Probá de nuevo.')
    } finally {
      setBusy(false)
    }
  }
  // A new account goes on to choose its plan, exactly as one made in Registro.
  const afterGoogle = (outcome: 'authenticated' | 'created') => {
    if (outcome === 'created') navigate('/registro', { state: { step: 'plan' } })
    else navigate(from, { replace: true })
  }
  const onGoogle = async (credential: string) => {
    setBusy(true)
    setError('')
    try {
      afterGoogle(await api.signInWithGoogle(credential, { remember }))
    } catch (err) {
      if (err instanceof ApiError && err.code === 'terms_required') {
        // First time with this Google account: no account without consent.
        setGoogleCredential(credential)
        setAccepted(false)
        setView('google-terms')
      } else {
        setError(err instanceof ApiError ? err.message : 'No pudimos ingresar con Google. Probá de nuevo.')
      }
    } finally {
      setBusy(false)
    }
  }
  const onGoogleTerms = async () => {
    if (!googleCredential || !accepted || busy) return
    setBusy(true)
    setError('')
    try {
      afterGoogle(
        await api.signInWithGoogle(googleCredential, { remember, acceptedTermsVersion: TERMS_VERSION }),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'No pudimos crear la cuenta. Probá de nuevo.')
    } finally {
      setBusy(false)
    }
  }
  const onResend = async () => {
    if (cooldown.left > 0) return
    setError('')
    setNotice('')
    try {
      await api.resendCode()
      setCode(['', '', '', '', '', ''])
      setNotice('Te enviamos un código nuevo.')
      cooldown.start()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'No pudimos reenviar el código.')
    }
  }

  return (
    <div className={auth.auth}>
      <aside className={auth.authBrand}>
        <Link className={auth.abTop} to="/">
          <AlibiMark size={30} />ALIBI
        </Link>
        <div className={auth.abMid}>
          <div className={auth.abQuote}>
            Tus datos ya tienen las respuestas. Te ayudamos a preguntarlas.
          </div>
          <div className={auth.abBy}>
            <Icon icon={ShieldCheck} size={20} color="var(--accent)" />
            <div>Solo lectura · nunca modificamos tus datos</div>
          </div>
        </div>
        <div className={auth.abFeats}>
          <div className={auth.abFeat}>
            <Icon icon={Lightning} color="var(--accent)" />Del archivo al gráfico en segundos
          </div>
          <div className={auth.abFeat}>
            <Icon icon={ChatsCircle} color="var(--accent)" />Una conversación por fuente de datos
          </div>
        </div>
      </aside>

      <main className={auth.authMain}>
        <div className={auth.authCard}>
          {view === 'login' && (
            <div className="fade-up">
              <h1 className={auth.authH}>Te damos la bienvenida</h1>
              <p className={auth.authSub}>Iniciá sesión para seguir consultando tus datos.</p>
              {googleEnabled && (
                <>
                  <GoogleButton onCredential={onGoogle} />
                  <div className={auth.dividerOr}>o con tu email</div>
                </>
              )}
              <div className={auth.formFields}>
                <Field
                  label="Email"
                  placeholder="vos@empresa.com"
                  value={email}
                  onChange={onEmail}
                  onBlur={onEmailBlur}
                  error={emailErr ? 'Ingresá un email válido' : undefined}
                />
                <div className="field-group">
                  <div className={styles.rowBetween} style={{ marginBottom: 7 }}>
                    <label className="field-label" style={{ margin: 0 }}>Contraseña</label>
                    <button type="button" className={styles.linkSm} onClick={() => setView('forgot')}>
                      ¿La olvidaste?
                    </button>
                  </div>
                  <Field icon={Lock} type="password" placeholder="Tu contraseña" value={pw} onChange={onPw} />
                </div>
              </div>
              <div className={styles.rowBetween} style={{ marginTop: 16 }}>
                <button
                  type="button"
                  className={cx(styles.check, remember && styles.on)}
                  aria-pressed={remember}
                  onClick={() => setRemember((v) => !v)}
                >
                  <span className={styles.checkBox}><Icon icon={Check} size={12} /></span>
                  Mantener la sesión iniciada
                </button>
              </div>
              <Button
                variant="primary"
                disabled={loginDisabled || busy}
                onClick={onLogin}
                style={{ width: '100%', justifyContent: 'center', marginTop: 20 }}
              >
                {busy ? <Spinner /> : <>Iniciar sesión<Icon icon={ArrowRight} size="sm" /></>}
              </Button>
              {error && (
                <div className="field-msg" style={{ justifyContent: 'center', marginTop: 10 }}>{error}</div>
              )}
              <div className={auth.authFoot}>
                ¿No tenés cuenta? <Link to="/registro">Creá una cuenta gratis</Link>
              </div>
            </div>
          )}

          {view === 'verify' && (
            <div className="fade-up" style={{ textAlign: 'center' }}>
              <div className={auth.mailIco}><Icon icon={EnvelopeSimple} size={26} /></div>
              <h1 className={auth.authH}>Verificá tu email</h1>
              <p className={auth.authSub}>
                Tu cuenta todavía no está verificada. Enviamos un código de 6 dígitos a<br />
                <span style={{ color: 'var(--fg-1)', fontWeight: 500 }}>{emailOr}</span>
              </p>
              <CodeInput value={code} onChange={setCode} aria-label="Código de verificación" />
              <Button
                variant="primary"
                disabled={codeDisabled || busy}
                onClick={onVerify}
                style={{ width: '100%', justifyContent: 'center', marginTop: 24 }}
              >
                {busy ? <Spinner /> : <>Verificar e iniciar sesión<Icon icon={ArrowRight} size="sm" /></>}
              </Button>
              {error && (
                <div className="field-msg" style={{ justifyContent: 'center', marginTop: 10 }}>{error}</div>
              )}
              {notice && (
                <div className="field-msg" style={{ justifyContent: 'center', marginTop: 10, color: 'var(--accent)' }}>{notice}</div>
              )}
              <div className={auth.resend}>
                ¿No te llegó?{' '}
                {cooldown.left > 0 ? (
                  <span>Podés pedir otro en {cooldown.label}</span>
                ) : (
                  <button type="button" className={styles.linkSm} onClick={onResend}>Reenviar código</button>
                )}
              </div>
              <div className={auth.authFoot}>
                <button type="button" className={styles.linkSm} onClick={back}>← Volver a iniciar sesión</button>
              </div>
            </div>
          )}

          {view === 'google-terms' && (
            <div className="fade-up">
              <h1 className={auth.authH}>Creá tu cuenta</h1>
              <p className={auth.authSub}>
                Es la primera vez que entrás con esta cuenta de Google. Para crear tu cuenta de ALIBI, aceptá los
                términos.
              </p>
              <TermsConsent checked={accepted} onChange={setAccepted} />
              <Button
                variant="primary"
                disabled={!accepted || busy}
                onClick={onGoogleTerms}
                style={{ width: '100%', justifyContent: 'center', marginTop: 22 }}
              >
                {busy ? <Spinner /> : <>Crear cuenta<Icon icon={ArrowRight} size="sm" /></>}
              </Button>
              {error && (
                <div className="field-msg" style={{ justifyContent: 'center', marginTop: 10 }}>{error}</div>
              )}
              <div className={auth.authFoot}>
                <button type="button" className={styles.linkSm} onClick={back}>← Volver a iniciar sesión</button>
              </div>
            </div>
          )}

          {view === 'forgot' && (
            <div className="fade-up">
              <h1 className={auth.authH}>Restablecer contraseña</h1>
              <p className={auth.authSub}>Ingresá tu email y te enviaremos las instrucciones para crear una nueva.</p>
              <Field
                label="Email"
                placeholder="vos@empresa.com"
                value={email}
                onChange={onEmail}
                onBlur={onEmailBlur}
                error={emailErr ? 'Ingresá un email válido' : undefined}
              />
              <Button
                variant="primary"
                disabled={forgotDisabled}
                onClick={() => validEmail(email) && setView('forgot-sent')}
                style={{ width: '100%', justifyContent: 'center', marginTop: 20 }}
              >
                Enviar instrucciones<Icon icon={PaperPlaneTilt} size="sm" />
              </Button>
              <div className={auth.authFoot}>
                <button type="button" className={styles.linkSm} onClick={back}>← Volver a iniciar sesión</button>
              </div>
            </div>
          )}

          {view === 'forgot-sent' && (
            <div className="fade-up" style={{ textAlign: 'center' }}>
              <div className={auth.mailIco}><Icon icon={EnvelopeSimple} size={26} /></div>
              <h1 className={auth.authH}>Listo — revisá tu email</h1>
              <p className={auth.authSub}>
                Si <span style={{ color: 'var(--fg-1)', fontWeight: 500 }}>{emailOr}</span> tiene una cuenta, vas a recibir un enlace para restablecer la contraseña.
              </p>
              <button type="button" className="btn btn-glass" style={{ width: '100%', justifyContent: 'center' }} onClick={back}>
                Volver a iniciar sesión
              </button>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
