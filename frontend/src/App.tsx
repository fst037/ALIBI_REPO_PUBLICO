import { RouterProvider } from 'react-router-dom'
import { AuthProvider } from './features/auth/AuthContext'
import { DbWakeNotice } from './features/db/DbWakeNotice'
import { LegalProvider } from './features/legal/LegalProvider'
import { router } from './router'

export default function App() {
  return (
    <AuthProvider>
      {/* Above the router: Terms and Privacy open as a modal over any screen. */}
      <LegalProvider>
        <RouterProvider router={router} />
        <DbWakeNotice />
      </LegalProvider>
    </AuthProvider>
  )
}
