/**
 * "Continuar con Google" — Google Identity Services (GIS) in the browser.
 *
 * GIS draws Google's own button and, when the person picks an account, hands
 * back an ID token (a signed JWT). That token is all the backend needs
 * (`POST /auth/google` checks it against Google's public keys): no redirect, no
 * OAuth code, no client secret. The button is Google's, not restyled to match
 * `.btn` — GIS issues ID tokens only from its own button (or One Tap), and its
 * branding guidelines ask for it.
 *
 * The OAuth client id is baked in at build time (`VITE_GOOGLE_CLIENT_ID`, the
 * same value as the API's `ALIBI_GOOGLE_CLIENT_ID`). Unset, `googleEnabled` is
 * false and the screens show only the email path — local dev needs no Google
 * project.
 */

const SCRIPT_SRC = 'https://accounts.google.com/gsi/client'

export const GOOGLE_CLIENT_ID: string = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? ''
export const googleEnabled = GOOGLE_CLIENT_ID.length > 0

/* The slice of the GIS API this app uses (developers.google.com/identity/gsi/web/reference/js-reference). */
export interface GsiCredentialResponse {
  credential: string
}

export interface GsiButtonOptions {
  type?: 'standard' | 'icon'
  theme?: 'outline' | 'filled_blue' | 'filled_black'
  size?: 'large' | 'medium' | 'small'
  text?: 'signin_with' | 'signup_with' | 'continue_with' | 'signin'
  shape?: 'rectangular' | 'pill' | 'circle' | 'square'
  logo_alignment?: 'left' | 'center'
  /** Pixels, 200–400. */
  width?: number
  locale?: string
}

export interface GsiId {
  initialize(config: {
    client_id: string
    callback: (response: GsiCredentialResponse) => void
    context?: 'signin' | 'signup' | 'use'
    ux_mode?: 'popup' | 'redirect'
  }): void
  renderButton(parent: HTMLElement, options: GsiButtonOptions): void
}

declare global {
  interface Window {
    google?: { accounts?: { id?: GsiId } }
  }
}

let loading: Promise<GsiId> | null = null

/** Load the GIS script once per page; later calls share the same promise. */
export function loadGoogleIdentity(): Promise<GsiId> {
  const ready = window.google?.accounts?.id
  if (ready) return Promise.resolve(ready)
  if (!loading) {
    loading = new Promise<GsiId>((resolve, reject) => {
      const script = document.createElement('script')
      script.src = SCRIPT_SRC
      script.async = true
      script.onload = () => {
        const gsi = window.google?.accounts?.id
        if (gsi) resolve(gsi)
        else reject(new Error('Google Identity Services did not initialise.'))
      }
      script.onerror = () => {
        // Forget the failure, so a later mount (or a reload of the screen) retries.
        loading = null
        script.remove()
        reject(new Error('Google Identity Services could not be loaded.'))
      }
      document.head.appendChild(script)
    })
  }
  return loading
}
