import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RequireAuth } from './RequireAuth'

// A signed-out visitor who opens a private screen is sent
// to the sign-in screen, and the place they wanted is remembered.

const auth = vi.hoisted(() => ({ value: { user: null, loading: false, signOut: async () => {} } as unknown }))
const session = vi.hoisted(() => ({ signedIn: false }))

vi.mock('./AuthContext', () => ({ useAuth: () => auth.value }))
vi.mock('../../lib/api/session', () => ({ isAuthenticated: () => session.signedIn }))

function LoginProbe() {
  const { state } = useLocation()
  return <div>pantalla de acceso desde {(state as { from?: { pathname: string } } | null)?.from?.pathname}</div>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<LoginProbe />} />
        <Route element={<RequireAuth />}>
          <Route path="/biblioteca" element={<div>contenido privado</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  session.signedIn = false
  auth.value = { user: null, loading: false, signOut: async () => {} }
})

describe('RequireAuth', () => {
  it('sends a visitor without a session to the sign-in screen, remembering where they were going', () => {
    renderAt('/biblioteca')
    expect(screen.getByText('pantalla de acceso desde /biblioteca')).toBeTruthy()
    expect(screen.queryByText('contenido privado')).toBeNull()
  })

  it('lets a signed-in user through', () => {
    session.signedIn = true
    auth.value = { user: { id: 'u_ana' }, loading: false, signOut: async () => {} }
    renderAt('/biblioteca')
    expect(screen.getByText('contenido privado')).toBeTruthy()
  })
})
