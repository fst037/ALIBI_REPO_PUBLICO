import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { Spinner } from '../../components/ui'
import { isAuthenticated } from '../../lib/api/session'
import { useAuth } from './AuthContext'

/** Full-screen loading state while the session bootstraps. */
export function AuthSplash() {
  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        background: 'var(--bg-0, #0a0a0c)',
      }}
    >
      <Spinner />
    </div>
  )
}

/**
 * Route guard for authenticated screens. Redirects to /login (remembering the
 * intended destination) when there is no session, and shows a splash while the
 * token is being validated.
 */
export function RequireAuth() {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) return <AuthSplash />
  if (!isAuthenticated()) return <Navigate to="/login" state={{ from: location }} replace />
  // Token present but the user is still resolving (e.g. just after login).
  if (!user) return <AuthSplash />
  return <Outlet />
}
