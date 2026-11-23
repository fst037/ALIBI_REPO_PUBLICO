import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Database, Swap } from '@phosphor-icons/react'
import { AppShell } from '../components/layout/AppShell'
import { Button, Icon, Spinner } from '../components/ui'
import * as api from '../lib/api'
import type { RunMode } from '../lib/api'
import type { ChatSource, Dataset, Folder } from '../types'
import { useConversation } from '../features/conversation/useConversation'
import { folderSource, sourceLabel as labelOf } from '../features/conversation/source'
import { SourcePicker } from '../features/conversation/SourcePicker'
import { Greeting } from '../features/conversation/Greeting'
import { MessageThread } from '../features/conversation/MessageThread'
import { Composer } from '../features/conversation/Composer'
import { usePlan } from '../features/billing/usePlan'
import { SourceSwitchModal } from '../features/conversation/SourceSwitchModal'
import styles from '../features/conversation/conversation.module.css'

type View = 'source' | 'chat' | 'opening'

const EMPTY_SOURCE: ChatSource = { type: 'folder', id: '', name: '', datasets: [] }

/** The conversational canvas: choose sources, then ask questions. */
export function Conversacion() {
  const [params, setParams] = useSearchParams()
  // Deep-linked arrivals start on `opening`, never on the source picker. The
  // screen used to mount on `source` and switch once the fetch resolved, so
  // clicking a conversation flashed "start a new query" before showing the one
  // that was clicked — a fetch's worth of the wrong screen.
  const [view, setView] = useState<View>(() =>
    params.get('conversation') || params.get('folder') ? 'opening' : 'source',
  )
  // The whole descriptor, not just the datasets: a folder-sourced thread has to
  // stay a folder all the way to the API, or it silently freezes to a list of ids.
  const [source, setSource] = useState<ChatSource>(EMPTY_SOURCE)
  const [input, setInput] = useState('')
  const [switchOpen, setSwitchOpen] = useState(false)
  const { messages, send, addSystemNotice, reset, load, setSource: setThreadSource, mode, setMode } =
    useConversation()

  // Deep is a Pro feature, within the monthly quota. Until the plan has loaded the switch stays usable; the
  // backend has the last word (402).
  const { plan, sub } = usePlan()
  const overQuota = !!sub && sub.usage.queries.limit !== null && sub.usage.queries.used >= sub.usage.queries.limit
  const deepLock: 'plan' | 'quota' | null =
    plan && !plan.limits.answerModes.includes('deep') ? 'plan' : overQuota ? 'quota' : null
  const deepAllowed = deepLock === null

  const sourceLabel = labelOf(source)
  const activeSources = source.datasets

  const startChat = (next: ChatSource) => {
    setSource(next)
    setThreadSource(next)
    reset()
    setView('chat')
  }

  // Deep-link from History and the sidebar's "Recent" list
  // (`/app?conversation=<id>`): reopen that thread with its messages and its
  // folder, and tell the seam to keep writing into it instead of opening a
  // second conversation over the same folder.
  const conversationParam = params.get('conversation')
  useEffect(() => {
    if (!conversationParam) return
    let alive = true
    // Also set here, not only in the initial state: clicking a recent from the
    // sidebar while the canvas is already mounted changes the param without
    // remounting, and the previous thread would otherwise stay on screen until
    // the new one arrived.
    setView('opening')
    void (async () => {
      const conversation = await api.getConversation(conversationParam).catch(() => null)
      if (!alive) return
      if (!conversation?.source) {
        // Deleted, or someone else's. The source picker is where to recover
        // from that, and leaving the spinner up forever is not.
        setView('source')
        setParams({}, { replace: true })
        return
      }
      // The folder is re-read so the source pills show its current contents —
      // the whole point of a folder source. If it was deleted since, the thread
      // still reopens and stays readable; only asking again fails, with the
      // server's own message.
      const detail = conversation.source.id ? await api.getFolder(conversation.source.id) : null
      if (!alive) return
      const next: ChatSource = { ...conversation.source, datasets: detail?.datasets ?? [] }
      setSource(next)
      setThreadSource(next)
      load(conversation.messages, conversation.mode)
      api.adoptConversation(conversation.id, next)
      setView('chat')
      // Dropped so a later "New query" reset isn't undone by the URL.
      setParams({}, { replace: true })
    })()
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationParam])

  // Deep-link from the library, now `/app?folder=<id>`: load that folder and
  // jump straight into the chat over it.
  //
  // The old `?dataset=<id>` form is gone with the dataset source type — there is
  // no longer a conversation shape that a single loose file could open. A stale
  // link lands on the source picker, which is the right place to recover.
  const folderParam = params.get('folder')
  useEffect(() => {
    if (!folderParam) return
    let alive = true
    setView('opening')
    void api.getFolder(folderParam).catch(() => null).then((detail) => {
      if (!alive) return
      if (!detail || detail.datasets.length === 0) {
        setView('source')
      } else {
        startChat(folderSource(detail, detail.datasets))
      }
      // Drop the query param so a later "New query" reset isn't overridden.
      setParams({}, { replace: true })
    })
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folderParam])
  const newQuery = () => {
    setView('source')
    setSource(EMPTY_SOURCE)
    setThreadSource(EMPTY_SOURCE)
    setInput('')
    reset()
  }
  const onSend = (override?: RunMode) => {
    const q = input
    setInput('')
    // Without Deep every question goes Quick, including in a conversation saved
    // as Deep (the default itself is kept for when Deep is available again).
    void send(q, deepAllowed ? override : 'quick')
  }
  const confirmSwitch = (folder: Folder, datasets: Dataset[]) => {
    const next = folderSource(folder, datasets)
    setSource(next)
    setThreadSource(next)
    addSystemNotice(`Fuente cambiada a ${labelOf(next)} — se conserva el historial`)
    setSwitchOpen(false)
  }

  return (
    <AppShell onNewQuery={newQuery}>
      <div className={styles.canvas}>
        {view === 'opening' && (
          <div className={styles.opening} role="status" aria-live="polite">
            <Spinner />
            <span>Abriendo conversación…</span>
          </div>
        )}

        {view === 'source' && <SourcePicker onStart={startChat} />}

        {view === 'chat' && (
          <>
            <div className={styles.convTop}>
              <div className={styles.convSources}>
                <span className="hint" style={{ marginRight: 2 }}>Fuente:</span>
                {activeSources.map((s) => (
                  <span key={s.id} className="src-pill"><Icon icon={Database} size="sm" />{s.name}</span>
                ))}
                <Button variant="ghost" size="sm" onClick={() => setSwitchOpen(true)}>
                  <Icon icon={Swap} size="sm" />Cambiar
                </Button>
              </div>
            </div>

            <div className={styles.convScroll}>
              <div className={styles.convInner}>
                {messages.length === 0 ? (
                  <Greeting sourceLabel={sourceLabel} />
                ) : (
                  <MessageThread
                    messages={messages}
                    sourceLabel={sourceLabel}
                    datasetId={activeSources[0]?.id ?? ''}
                    datasetName={sourceLabel}
                  />
                )}
              </div>
            </div>

            <Composer value={input} onChange={setInput} onSend={onSend} mode={mode} onModeChange={setMode} deepLock={deepLock} />
          </>
        )}
      </div>

      <SourceSwitchModal
        open={switchOpen}
        current={source.id}
        onClose={() => setSwitchOpen(false)}
        onConfirm={confirmSwitch}
      />
    </AppShell>
  )
}
