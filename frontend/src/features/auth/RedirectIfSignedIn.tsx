import { Navigate, Outlet } from 'react-router-dom'
import { AuthSplash } from './RequireAuth'
import { useAuth } from './AuthContext'

/**
 * The mirror of `RequireAuth`: sends a signed-in visitor to the app instead of
 * showing them a public page they have no use for.
 *
 * It guards the site root. Typing the bare address is how people return to a
 * product they are already signed into, and landing on the marketing page there
 * reads as having been signed out — the session was fine, `/` just never asked.
 *
 * Anonymous visitors are not delayed by this: `AuthContext` only starts in the
 * loading state when a token already exists, so with no session the landing
 * renders on the first paint, as before. A token that turns out to be invalid
 * is cleared by the provider and the landing renders too.
 *
 * Deliberately NOT applied to `/login` (someone with a live session may be going
 * there to sign in as somebody else) or to `/registro` (registration signs the
 * new account in at its first step, and the remaining steps live on that same
 * route — a guard there would eject the user mid-signup).
 */
export function RedirectIfSignedIn() {
  const { user, loading } = useAuth()

  if (loading) return <AuthSplash />
  if (user) return <Navigate to="/app" replace />
  return <Outlet />
}
