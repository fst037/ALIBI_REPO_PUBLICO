import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Database, MagnifyingGlass, Star, Table, Trash } from '@phosphor-icons/react'
import { AppShell } from '../components/layout/AppShell'
import { Button, CHART_TYPES, EmptyState, Field, Icon, Modal, Segmented, Skeleton, Spinner } from '../components/ui'
import type { ChartType } from '../components/ui'
import { conversationTitle } from '../features/conversation/title'
import { CONVERSATIONS_CHANGED } from '../features/conversation/useConversation'
import * as api from '../lib/api'
import type { HistoryGroup, HistoryItem } from '../types'
import { cx } from '../lib/cx'
import styles from './Historial.module.css'

type Filter = 'all' | 'fav'
const FILTER_OPTIONS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'Todas' },
  { value: 'fav', label: 'Favoritas' },
]

const chartIcon = (t: ChartType) => CHART_TYPES.find((c) => c.key === t)?.icon ?? Table

export function Historial() {
  const [groups, setGroups] = useState<HistoryGroup[]>([])
  // True until the first fetch settles, so "no conversations yet" is never shown
  // for a list that simply hasn't arrived.
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  // The conversation the delete dialog is about; null while it is closed.
  const [pendingDelete, setPendingDelete] = useState<HistoryItem | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  // Stars mid-flight, so a double click can't fire two opposite writes.
  const [starring, setStarring] = useState<Set<string>>(new Set())

  const refresh = async () => {
    setGroups(await api.listHistory())
  }

  useEffect(() => {
    let alive = true
    void api
      .listHistory()
      .then((g) => alive && setGroups(g))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  /** Patch one row in place — the whole list would flicker for a single star. */
  const patchItem = (id: string, patch: Partial<HistoryItem>) => {
    setGroups((gs) =>
      gs.map((g) => ({ ...g, items: g.items.map((i) => (i.id === id ? { ...i, ...patch } : i)) })),
    )
  }

  const toggleFavorite = async (item: HistoryItem) => {
    if (starring.has(item.id)) return
    const next = !item.favorite
    setStarring((s) => new Set(s).add(item.id))
    setActionError(null)
    // Optimistic: a star has to feel instant. Reverted below if the write fails,
    // because a star that silently didn't save is worse than one that flickers.
    patchItem(item.id, { favorite: next })
    try {
      await api.setConversationFavorite(item.id, next)
    } catch {
      patchItem(item.id, { favorite: item.favorite })
      setActionError('No pudimos actualizar esa conversación. Probá de nuevo.')
    } finally {
      setStarring((s) => {
        const copy = new Set(s)
        copy.delete(item.id)
        return copy
      })
    }
  }

  const confirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    setActionError(null)
    try {
      await api.deleteConversation(pendingDelete.id)
      setPendingDelete(null)
      // A refetch, not a local filter: emptying a group changes which date
      // headings exist, and the server is what decides that.
      await refresh()
      // The sidebar's Recent list holds the same conversations.
      window.dispatchEvent(new Event(CONVERSATIONS_CHANGED))
    } catch {
      setActionError('No pudimos eliminar la conversación. Probá de nuevo.')
    } finally {
      setDeleting(false)
    }
  }

  const q = query.trim().toLowerCase()
  const filtered = groups
    .map((g) => ({
      date: g.date,
      // Searched on the *displayed* title, so what the user reads is what they
      // can type: an untitled conversation shows its folder, not "New query".
      items: g.items.filter(
        (c) =>
          (filter === 'fav' ? c.favorite : true) &&
          (!q ||
            conversationTitle(c.title, c.source).toLowerCase().includes(q) ||
            c.source.toLowerCase().includes(q)),
      ),
    }))
    .filter((g) => g.items.length > 0)

  const nothingAtAll = groups.every((g) => g.items.length === 0)

  return (
    <AppShell title="Chats" activeNav="historial">
      <div className="page-scroll">
        <div className="page-pad">
          <div className="page-head">
            <h1 className="page-h">Chats</h1>
            <p className="page-sub">Retomá cualquier conversación donde la dejaste. Todo tu historial, con búsqueda.</p>
          </div>

          <div className={styles.filters}>
            <div className="grow" style={{ maxWidth: 360 }}>
              <Field icon={MagnifyingGlass} placeholder="Buscar en tus conversaciones…" value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
            <Segmented options={FILTER_OPTIONS} value={filter} onChange={setFilter} aria-label="Filtro" />
          </div>

          {actionError && (
            <p className={styles.error} role="alert">{actionError}</p>
          )}

          {loading ? (
            <div className={styles.group} aria-busy="true" aria-label="Cargando conversaciones">
              <Skeleton width={90} height={11} style={{ marginBottom: 12 }} />
              <div className={styles.list}>
                {Array.from({ length: 5 }, (_, i) => (
                  <div key={i} className={styles.item}>
                    <Skeleton variant="block" width={40} height={40} style={{ borderRadius: 'var(--r-sm)', flex: 'none' }} />
                    <div className="grow" style={{ minWidth: 0 }}>
                      <Skeleton width={`${45 + (i % 3) * 15}%`} height={14} style={{ marginBottom: 9 }} />
                      <Skeleton width={`${30 + (i % 2) * 10}%`} height={11} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : filtered.length > 0 ? (
            filtered.map((g) => (
              <div key={g.date} className={styles.group}>
                <div className={styles.date}>{g.date}</div>
                <div className={styles.list}>
                  {g.items.map((c) => (
                    // The row is not itself a link any more: the star and the
                    // trash are real buttons, and a button inside an anchor is
                    // both invalid markup and a click the browser routes wrong.
                    <div key={c.id} className={styles.item}>
                      <Link className={styles.open} to={`/app?conversation=${encodeURIComponent(c.id)}`}>
                        <div className={styles.ico}><Icon icon={chartIcon(c.chartType)} size={19} /></div>
                        <div className="grow" style={{ minWidth: 0 }}>
                          <div className={styles.title}>{conversationTitle(c.title, c.source)}</div>
                          <div className={styles.meta}>
                            <span className={styles.src}><Icon icon={Database} size={11} />{c.source}</span>
                            <span className={styles.sep} />
                            <span>{c.questionCount} {c.questionCount === 1 ? 'pregunta' : 'preguntas'}</span>
                            <span className={styles.sep} />
                            <span>{c.time}</span>
                          </div>
                        </div>
                      </Link>
                      {/* Kept visible while starred — a favorite the user has to
                          hover to see is not a favorite they can scan for. */}
                      <div className={cx(styles.actions, c.favorite && styles.actionsPinned)}>
                        <button
                          type="button"
                          className="btn btn-ghost btn-icon"
                          onClick={() => void toggleFavorite(c)}
                          disabled={starring.has(c.id)}
                          aria-pressed={c.favorite}
                          aria-label={c.favorite ? 'Quitar de favoritas' : 'Agregar a favoritas'}
                          title={c.favorite ? 'Quitar de favoritas' : 'Agregar a favoritas'}
                        >
                          <Icon
                            icon={Star}
                            size="sm"
                            weight={c.favorite ? 'fill' : 'regular'}
                            color={c.favorite ? 'var(--accent)' : undefined}
                          />
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-icon"
                          onClick={() => { setActionError(null); setPendingDelete(c) }}
                          aria-label="Eliminar conversación"
                          title="Eliminar conversación"
                        >
                          <Icon icon={Trash} size="sm" />
                        </button>
                      </div>
                      <Icon icon={ArrowRight} size="sm" color="var(--fg-4)" />
                    </div>
                  ))}
                </div>
              </div>
            ))
          ) : (
            <div className={cx('card')} style={{ marginTop: 20 }}>
              {nothingAtAll ? (
                <EmptyState
                  icon={MagnifyingGlass}
                  title="Todavía no hay conversaciones"
                  description="Hacé una pregunta sobre una de tus carpetas y va a aparecer acá."
                  action={<Link className="btn btn-primary btn-sm" to="/app">Nueva consulta</Link>}
                />
              ) : (
                <EmptyState
                  icon={MagnifyingGlass}
                  title="Sin resultados"
                  description={
                    filter === 'fav' && !q
                      ? 'Todavía no marcaste ninguna conversación como favorita.'
                      : `No encontramos conversaciones que coincidan con “${query}”.`
                  }
                  action={
                    q ? (
                      <Button variant="glass" size="sm" onClick={() => setQuery('')}>Borrar búsqueda</Button>
                    ) : undefined
                  }
                />
              )}
            </div>
          )}
        </div>
      </div>

      <Modal
        open={pendingDelete !== null}
        onClose={() => !deleting && setPendingDelete(null)}
        showClose={false}
        aria-label="Confirmar eliminación"
      >
        <div className={styles.confirm}>
          <div className={styles.confirmIco}><Icon icon={Trash} size={22} /></div>
          <h3 className={styles.confirmTitle}>
            ¿Eliminar “{pendingDelete ? conversationTitle(pendingDelete.title, pendingDelete.source) : ''}”?
          </h3>
          <p className={styles.confirmText}>
            Esto elimina la conversación y todas sus preguntas y respuestas. Tus archivos y
            carpetas no se tocan. Esta acción no se puede deshacer.
          </p>
          {actionError && (
            <p className={styles.confirmText} style={{ color: 'var(--danger)' }}>{actionError}</p>
          )}
          <div className={styles.confirmActions}>
            <Button variant="glass" onClick={() => setPendingDelete(null)} disabled={deleting}>No</Button>
            <Button variant="glass" className={styles.danger} onClick={() => void confirmDelete()} disabled={deleting}>
              {deleting ? <Spinner /> : 'Sí, eliminar'}
            </Button>
          </div>
        </div>
      </Modal>
    </AppShell>
  )
}
