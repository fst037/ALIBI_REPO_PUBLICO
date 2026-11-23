import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell, CheckCircle, Checks, Sparkle, Warning, XCircle } from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import * as api from '../../lib/api'
import type { Notification, NotificationTone } from '../../types'
import { Icon } from '../ui'
import { cx } from '../../lib/cx'
import styles from './NotificationsMenu.module.css'

const TONE_ICON: Record<NotificationTone, PhosphorIcon> = {
  ok: CheckCircle,
  warn: Warning,
  info: Sparkle,
  err: XCircle,
}

const POLL_MS = 60_000

/** Bell button with a dropdown of notifications (no separate page). */
export function NotificationsMenu() {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [notifs, setNotifs] = useState<Notification[]>([])
  const ref = useRef<HTMLDivElement>(null)

  // Notices arrive while the user works (an upload finishing, a quota crossed),
  // so the list is refreshed on a slow poll, when the tab regains focus and
  // whenever the panel opens. Failures are silent: the bell is never a blocker.
  useEffect(() => {
    let alive = true
    const refresh = () => {
      if (document.visibilityState === 'hidden') return
      api.listNotifications().then((n) => alive && setNotifs(n)).catch(() => {})
    }
    refresh()
    const timer = window.setInterval(refresh, POLL_MS)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      alive = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const unread = notifs.filter((n) => !n.read).length
  const markAll = () => {
    void api.markAllRead().then(setNotifs)
  }
  const toggle = () => {
    if (!open) api.listNotifications().then(setNotifs).catch(() => {})
    setOpen((v) => !v)
  }
  const readOne = (n: Notification) => {
    if (!n.read) void api.markAsRead(n.id).then(setNotifs).catch(() => {})
  }
  // Acting on a notice settles it: mark it read, then follow its link.
  const goAction = (n: Notification) => {
    readOne(n)
    setOpen(false)
    if (n.action) navigate(n.action.href)
  }

  return (
    <div className={styles.wrap} ref={ref}>
      <button
        type="button"
        className="btn btn-glass btn-icon"
        aria-label="Notificaciones"
        aria-expanded={open}
        onClick={toggle}
      >
        <Icon icon={Bell} size="sm" />
        {unread > 0 && <span className={styles.badge}>{unread > 9 ? '9+' : unread}</span>}
      </button>

      {open && (
        <div className={styles.panel} role="menu">
          <div className={styles.head}>
            <span className={styles.title}>Notificaciones</span>
            <button type="button" className={styles.markAll} onClick={markAll} disabled={unread === 0}>
              <Icon icon={Checks} size="sm" />Marcar todo como leído
            </button>
          </div>
          <div className={styles.list}>
            {notifs.length === 0 ? (
              <div className={styles.empty}>No tenés notificaciones.</div>
            ) : (
              notifs.map((n) => (
                <div key={n.id} className={cx(styles.item, !n.read && styles.unread)} onClick={() => readOne(n)}>
                  <div className={cx(styles.ico, styles[n.tone])}>
                    <Icon icon={TONE_ICON[n.tone]} size={18} weight="fill" />
                  </div>
                  <div className="grow">
                    <p className={styles.t}>{n.title}</p>
                    <p className={styles.b}>{n.body}</p>
                    {n.action && (
                      <button type="button" className={styles.action} onClick={(e) => {
                          e.stopPropagation()
                          goAction(n)
                        }}>
                        {n.action.label} →
                      </button>
                    )}
                  </div>
                  <span className={styles.time}>{n.time}</span>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
