/* ============================================================
   "The database is waking up" — app-wide state.

   The RDS instance stops itself after an idle hour to save cost, and the
   first request afterwards gets a 503 `db_waking` while it starts (~5-10
   min; see backend/app/db/waker.py). `unwrap` reports that here instead of
   each screen inventing its own copy for it; `DbWakeNotice` shows it and
   this module polls `/health/db` until the database answers.
   ============================================================ */

export type DbWakeState = 'idle' | 'waking' | 'no_capacity' | 'ready' | 'failed'

const POLL_MS = 15_000
// RDS documents up to ~10 min to start; past this something else is wrong.
const GIVE_UP_MS = 20 * 60_000

let state: DbWakeState = 'idle'
const listeners = new Set<() => void>()

function set(next: DbWakeState) {
  state = next
  listeners.forEach((l) => l())
}

export function getDbWakeState(): DbWakeState {
  return state
}

export function subscribeDbWake(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Called by `unwrap` on a 503 `db_waking` / `db_no_capacity`. Starts one poll loop, however many calls land. */
export function notifyDbWaking(kind: 'waking' | 'no_capacity' = 'waking') {
  if (state === 'waking' || state === 'no_capacity') {
    if (state !== kind) set(kind)
    return
  }
  set(kind)
  void poll(Date.now())
}

async function poll(startedAt: number) {
  while (Date.now() - startedAt < GIVE_UP_MS) {
    await new Promise((r) => setTimeout(r, POLL_MS))
    try {
      // Every failed poll also nudges the backend to start the instance, which
      // is harmless: it only starts one that is actually stopped.
      const res = await fetch('/api/v1/health/db')
      if (res.ok) {
        set('ready')
        return
      }
      // AWS had no room for the instance: say so, and keep retrying (each poll retries the start).
      const body = (await res.json().catch(() => null)) as { error?: { code?: string } } | null
      set(body?.error?.code === 'db_no_capacity' ? 'no_capacity' : 'waking')
    } catch {
      // Network blip — keep waiting.
    }
  }
  set('failed')
}

export function dismissDbWake() {
  set('idle')
}
