import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import * as api from '../../lib/api'
import { clearSession, isAuthenticated, subscribeSession } from '../../lib/api/session'
import type { User } from '../../types'

interface AuthValue {
  /** The authenticated user, or null when signed out / still resolving. */
  user: User | null
  /** True while the initial session bootstrap (or a re-fetch) is in flight. */
  loading: boolean
  /** End the session server-side and locally; consumers redirect on the state change. */
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

/**
 * Owns the session lifecycle. On mount (and whenever a token appears via
 * login/email verification), it validates the token by loading `/auth/me`; when the token is
 * cleared (logout, or a 401 handled in `http.ts`), it drops the user. Route
 * guards and the shell read `user`/`loading` from here.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState<boolean>(isAuthenticated())

  useEffect(() => {
    let alive = true

    const load = async () => {
      if (!isAuthenticated()) {
        setUser(null)
        setLoading(false)
        return
      }
      setLoading(true)
      try {
        const u = await api.getCurrentUser()
        if (alive) setUser(u)
      } catch {
        // Stale/invalid token — drop it so guards redirect to login.
        if (alive) {
          clearSession()
          setUser(null)
        }
      } finally {
        if (alive) setLoading(false)
      }
    }

    void load()
    const unsubscribe = subscribeSession((token) => {
      if (token) void load()
      else setUser(null)
    })
    return () => {
      alive = false
      unsubscribe()
    }
  }, [])

  const signOut = useCallback(async () => {
    await api.logout()
  }, [])

  return <AuthContext.Provider value={{ user, loading, signOut }}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
