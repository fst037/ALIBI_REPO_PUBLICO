/* ============================================================
   Session/token storage for the real API.
   Holds the bearer token returned by login or email verification.
   In-memory first (fast, survives within the tab), backed by
   web storage so the session survives a reload. `lib/api/http`
   reads the token from here to authorize requests; `logout`
   (and any 401 handler) clears it.

   WHERE it is persisted is the client's half of "Keep me
   signed in":

   - checked   -> localStorage. Survives closing the browser,
                  and ends only when the user signs out —
                  matching a server session issued with no
                  expiry at all.
   - unchecked -> sessionStorage. Dies with the tab, so a
                  borrowed or shared machine doesn't keep the
                  session after the window is closed.

   Both are read on startup, localStorage first, so an existing
   session is found wherever it was left.
   ============================================================ */

const STORAGE_KEY = 'alibi.session.token'

let token: string | null = null

/** Subscribers notified whenever the token is set or cleared (e.g. AuthProvider). */
type SessionListener = (token: string | null) => void
const listeners = new Set<SessionListener>()

/** Subscribe to token changes; returns an unsubscribe function. */
export function subscribeSession(listener: SessionListener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

const durable = (): Storage | null => (typeof localStorage !== 'undefined' ? localStorage : null)
const perTab = (): Storage | null => (typeof sessionStorage !== 'undefined' ? sessionStorage : null)

/** The current bearer token, or null if the user isn't authenticated. */
export function getToken(): string | null {
  if (token !== null) return token
  token = durable()?.getItem(STORAGE_KEY) ?? perTab()?.getItem(STORAGE_KEY) ?? null
  return token
}

/**
 * Persist (or clear, with null) the bearer token, then notify subscribers.
 *
 * `remember` picks the store. The token is written to exactly one of the two —
 * both are cleared first — so a session that asked *not* to be remembered can
 * never leave a token behind in the durable store.
 */
export function setToken(next: string | null, remember = true): void {
  token = next
  durable()?.removeItem(STORAGE_KEY)
  perTab()?.removeItem(STORAGE_KEY)
  if (next) (remember ? durable() : perTab())?.setItem(STORAGE_KEY, next)
  listeners.forEach((l) => l(next))
}

/** True when there is a token (does not validate it server-side). */
export function isAuthenticated(): boolean {
  return getToken() !== null
}

/** Drop the session entirely (used by logout and on 401). */
export function clearSession(): void {
  setToken(null)
}
