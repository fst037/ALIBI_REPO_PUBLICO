import { useEffect, useRef, useState } from 'react'
import { GOOGLE_CLIENT_ID, loadGoogleIdentity } from './googleIdentity'
import styles from './GoogleButton.module.css'

export interface GoogleButtonProps {
  /** The ID token, once the person has picked a Google account. */
  onCredential: (credential: string) => void
  /** Only changes Google's own wording in its account chooser. */
  context?: 'signin' | 'signup'
}

/**
 * "Continuar con Google". GIS issues an ID token only from its own button, which
 * cannot be restyled, so the visible face is ours (same glass look as the rest
 * of the auth screens, Google's four-colour "G") and GIS's real button is drawn
 * over it, transparent, to take the click. Callers
 * render it only when `googleEnabled` (googleIdentity.ts). If Google's script
 * cannot load (blocked, offline), a one-line notice replaces it and the email
 * path below keeps working.
 */
export function GoogleButton({ onCredential, context = 'signin' }: GoogleButtonProps) {
  const slot = useRef<HTMLDivElement>(null)
  // GIS keeps the callback it was initialised with; this ref lets it always
  // reach the screen's current handler without re-rendering Google's button.
  const latest = useRef(onCredential)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    latest.current = onCredential
  }, [onCredential])

  useEffect(() => {
    let alive = true
    loadGoogleIdentity()
      .then((gsi) => {
        const el = slot.current
        if (!alive || !el) return
        gsi.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: (response) => latest.current(response.credential),
          context,
          ux_mode: 'popup',
        })
        gsi.renderButton(el, {
          type: 'standard',
          theme: 'outline',
          size: 'large',
          text: 'continue_with',
          shape: 'rectangular',
          logo_alignment: 'center',
          // GIS takes a fixed width in pixels, between 200 and 400.
          width: Math.max(200, Math.min(400, el.offsetWidth)),
          locale: 'es',
        })
      })
      .catch(() => {
        if (alive) setFailed(true)
      })
    return () => {
      alive = false
    }
  }, [context])

  if (failed) {
    return <div className={styles.unavailable}>No pudimos cargar el ingreso con Google. Usá tu email.</div>
  }
  return (
    <div className={styles.root}>
      <div className={styles.face} aria-hidden="true">
        <GoogleLogo />
        Continuar con Google
      </div>
      <div ref={slot} className={styles.slot} />
    </div>
  )
}

/** Google's "G" in its four brand colours (the mark must keep them). */
function GoogleLogo() {
  return (
    <svg className={styles.logo} viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  )
}
