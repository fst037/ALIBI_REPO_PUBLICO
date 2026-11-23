import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  ChatTeardropText,
  ClockCounterClockwise,
  Database,
  Lightning,
  Plus,
  Question,
  SquaresFour,
  UserCircle,
} from '@phosphor-icons/react'
import * as api from '../../lib/api'
import { CONVERSATIONS_CHANGED } from '../../features/conversation/useConversation'
import { conversationTitle } from '../../features/conversation/title'
import type { Conversation, PlanId, Subscription } from '../../types'
import { AlibiMark, Button, Icon, Skeleton, UsageBar } from '../ui'
import { cx } from '../../lib/cx'
import { NotificationsMenu } from './NotificationsMenu'
import { UserMenu } from './UserMenu'

export type NavKey = 'historial' | 'dashboards' | 'biblioteca' | 'cuenta'

const PLAN_LABEL: Record<PlanId, string> = { free: 'Free', pro: 'Pro' }

const recentLabel = (c: Conversation) => conversationTitle(c.title, c.source?.name)

export interface AppShellProps {
  children: ReactNode
  activeNav?: NavKey
  /** Page title shown on the left of the topbar. */
  title?: string
  /** Extra actions left of Help/notifications. */
  topbarActions?: ReactNode
  /** If provided, "New query" resets in place instead of navigating. */
  onNewQuery?: () => void
}

/** Persistent app frame (sidebar + topbar) for authenticated screens. */
export function AppShell({ children, activeNav, title, topbarActions, onNewQuery }: AppShellProps) {
  const [recents, setRecents] = useState<Conversation[]>([])
  // Only the first load shows a skeleton; later refreshes keep the list on screen.
  const [recentsLoading, setRecentsLoading] = useState(true)
  const [sub, setSub] = useState<Subscription | null>(null)

  useEffect(() => {
    let alive = true
    const loadRecents = () => {
      void api
        .getRecentConversations()
        .then((c) => alive && setRecents(c))
        .finally(() => alive && setRecentsLoading(false))
    }
    loadRecents()
    void api.getSubscription().then((s) => alive && setSub(s))
    // Answering a question on the canvas creates or names a conversation; this
    // is what keeps the list from showing the state it had at mount.
    window.addEventListener(CONVERSATIONS_CHANGED, loadRecents)
    return () => {
      alive = false
      window.removeEventListener(CONVERSATIONS_CHANGED, loadRecents)
    }
  }, [])

  const queries = sub?.usage.queries
  const planLabel = sub ? PLAN_LABEL[sub.planId] : ''

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sb-brand"><AlibiMark size={26} />ALIBI</div>
        {onNewQuery ? (
          <Button variant="primary" className="sb-new" onClick={onNewQuery}>
            <Icon icon={Plus} size="sm" />Nueva consulta
          </Button>
        ) : (
          <Link className="btn btn-primary sb-new" to="/app">
            <Icon icon={Plus} size="sm" />Nueva consulta
          </Link>
        )}

        <div className="sb-lab">Espacio de trabajo</div>
        <nav className="sb-nav">
          <Link className={cx('nav-item', activeNav === 'biblioteca' && 'active')} to="/biblioteca">
            <Icon icon={Database} className="i" />
            <span className="nav-text">
              <span className="nav-title">Biblioteca</span>
              <span className="nav-desc">Tus datasets y esquemas</span>
            </span>
          </Link>
          <Link className={cx('nav-item', activeNav === 'historial' && 'active')} to="/historial">
            <Icon icon={ClockCounterClockwise} className="i" />
            <span className="nav-text">
              <span className="nav-title">Chats</span>
              <span className="nav-desc">Tus consultas anteriores</span>
            </span>
          </Link>
          <Link className={cx('nav-item', activeNav === 'dashboards' && 'active')} to="/dashboards">
            <Icon icon={SquaresFour} className="i" />
            <span className="nav-text">
              <span className="nav-title">Dashboards</span>
              <span className="nav-desc">Tableros siempre al día</span>
            </span>
          </Link>
          <Link className={cx('nav-item', activeNav === 'cuenta' && 'active')} to="/cuenta">
            <Icon icon={UserCircle} className="i" />
            <span className="nav-text">
              <span className="nav-title">Cuenta</span>
              <span className="nav-desc">Perfil, plan y seguridad</span>
            </span>
          </Link>
        </nav>

        <div className="sb-lab">Recientes</div>
        <div className="sb-recent">
          {recentsLoading ? (
            <div aria-busy="true" aria-label="Cargando recientes" style={{ padding: '4px 10px', display: 'grid', gap: 12 }}>
              {[78, 62, 70].map((w, i) => (
                <Skeleton key={i} width={`${w}%`} height={12} />
              ))}
            </div>
          ) : recents.length > 0 ? (
            recents.map((c) => (
              <Link
                key={c.id}
                className="recent-item"
                to={`/app?conversation=${encodeURIComponent(c.id)}`}
                title={recentLabel(c)}
              >
                <Icon icon={ChatTeardropText} className="i" />
                <span className="t">{recentLabel(c)}</span>
              </Link>
            ))
          ) : (
            <div className="hint" style={{ padding: '2px 10px 4px' }}>Todavía no hay conversaciones</div>
          )}
        </div>

        <div className="sb-foot">
          <div className="plan-box">
            <div className="between" style={{ marginBottom: 9 }}>
              <span className="pill">Plan {planLabel}</span>
              {queries && (
                <span className="mono" style={{ fontSize: 11.5, color: 'var(--fg-3)' }}>
                  {queries.used.toLocaleString('es-AR')} / {queries.limit != null ? queries.limit.toLocaleString('es-AR') : '∞'}
                </span>
              )}
            </div>
            {queries && <UsageBar value={queries.used} max={queries.limit ?? queries.used} />}
            <div className="hint" style={{ marginTop: 8 }}>
              Consultas este mes
              {sub && sub.overage.queries > 0 && ` · +${sub.overage.queries.toLocaleString('es-AR')} adicionales`}
            </div>
            {sub?.planId === 'free' && (
              <Link className="btn btn-primary btn-sm" to="/cuenta?tab=billing" style={{ width: '100%', justifyContent: 'center', marginTop: 12 }}>
                <Icon icon={Lightning} size="sm" />Pasar a Pro
              </Link>
            )}
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="topbar2">
          {/* Empty when there is no title, so the actions stay on the right. */}
          {title ? <span className="page-title">{title}</span> : <span />}
          <div className="row" style={{ gap: 10 }}>
            {topbarActions}
            <NotificationsMenu />
            <UserMenu />
            <Link className="btn btn-ghost btn-sm" to="/ayuda"><Icon icon={Question} size="sm" />Ayuda</Link>
          </div>
        </div>
        {children}
      </main>
    </div>
  )
}
