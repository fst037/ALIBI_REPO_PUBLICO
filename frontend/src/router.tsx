import { createBrowserRouter, Navigate } from 'react-router-dom'
import { DocumentTitle } from './components/layout/DocumentTitle'
import { RedirectIfSignedIn } from './features/auth/RedirectIfSignedIn'
import { RequireAuth } from './features/auth/RequireAuth'
import { Landing } from './app/Landing'
import { Login } from './app/Login'
import { Registro } from './app/Registro'
import { Legal } from './app/Legal'
import { Onboarding } from './app/Onboarding'
import { Conversacion } from './app/Conversacion'
import { Ingesta } from './app/Ingesta'
import { Historial } from './app/Historial'
import { Biblioteca } from './app/Biblioteca'
import { Dashboards } from './app/Dashboards'
import { DashboardDetail } from './app/DashboardDetail'
import { Cuenta } from './app/Cuenta'
import { PagoResultado } from './app/PagoResultado'
import { Ayuda } from './app/Ayuda'
import { Estados } from './app/Estados'
import { DesignSystem } from './app/DesignSystem'

// `handle.title` names the route in the browser tab — `ALIBI - Library`. The
// wording follows what the screen already calls itself in the sidebar or its
// topbar, so the tab and the app agree. A route with no title (the landing,
// the /suscripcion redirect) leaves the tab as the bare brand. See
// components/layout/DocumentTitle.
export const router = createBrowserRouter([
  {
    element: <DocumentTitle />,
    children: [
      // The site root: the landing page for a visitor, the app for someone who is
      // already signed in (see RedirectIfSignedIn).
      {
        element: <RedirectIfSignedIn />,
        children: [{ path: '/', element: <Landing /> }],
      },

      // Public routes (no session required).
      { path: '/login', element: <Login />, handle: { title: 'Iniciar sesión' } },
      { path: '/registro', element: <Registro />, handle: { title: 'Registro' } },
      // The Terms and the Privacy Policy as pages with their own URL — the link
      // Google's consent screen needs (the in-app modal has none).
      { path: '/terminos', element: <Legal doc="terms" />, handle: { title: 'Términos y condiciones' } },
      { path: '/privacidad', element: <Legal doc="privacy" />, handle: { title: 'Política de privacidad' } },
      // Onboarding is reached mid-registration, before a session exists.
      { path: '/onboarding', element: <Onboarding />, handle: { title: 'Primeros pasos' } },
      // Dev/QA reference screens.
      { path: '/estados', element: <Estados />, handle: { title: 'Estados vacíos y de error' } },
      { path: '/dev/design-system', element: <DesignSystem />, handle: { title: 'Sistema de diseño' } },

      // Authenticated app (guarded by RequireAuth → redirects to /login).
      {
        element: <RequireAuth />,
        children: [
          { path: '/app', element: <Conversacion />, handle: { title: 'Conversación' } },
          { path: '/ingesta', element: <Ingesta />, handle: { title: 'Subir un archivo' } },
          { path: '/historial', element: <Historial />, handle: { title: 'Chats' } },
          { path: '/biblioteca', element: <Biblioteca />, handle: { title: 'Biblioteca' } },
          { path: '/dashboards', element: <Dashboards />, handle: { title: 'Dashboards' } },
          { path: '/dashboards/:dashboardId', element: <DashboardDetail />, handle: { title: 'Dashboard' } },
          { path: '/cuenta', element: <Cuenta />, handle: { title: 'Cuenta' } },
          // Where Mercado Pago sends the user back after paying for a plan.
          { path: '/pago/resultado', element: <PagoResultado />, handle: { title: 'Pago' } },
          // Subscription and billing now lives inside /cuenta; the old route is kept
          // redirect for old links and bookmarks.
          { path: '/suscripcion', element: <Navigate to="/cuenta?tab=billing" replace /> },
          { path: '/ayuda', element: <Ayuda />, handle: { title: 'Ayuda y soporte' } },
        ],
      },
    ],
  },
])
