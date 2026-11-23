/* ============================================================
   HTTP glue between the generated Orval client and the lib/api seam.

   The generated fetch client returns a discriminated `{ data, status,
   headers }` object and threads a `RequestInit` (`options`) into fetch.
   These helpers let each seam function stay a one-liner:

     export function getAccount() {
       return unwrap<User>(gen.getAccount(authInit()))
     }

   - `authInit`  attaches the bearer token (when present) as a header.
   - `unwrap`    throws `ApiError` on a >= 400 response, else returns `data`.

   Base URL: the generated URLs are baked with the spec's `/api/v1` prefix.
   In dev, Vite proxies `/api/v1` to Prism; in prod, route `/api/v1/*` to the
   API Gateway origin (CloudFront behavior) or switch to a mutator with an
   env-based base URL if a cross-origin call is preferred.
   ============================================================ */

import { notifyDbWaking } from './dbWake'
import { clearSession, getToken } from './session'

/** Shape of the standard error envelope: `{ error: { code, message, details? } }`. */
export interface ApiErrorDetail {
  field?: string
  message?: string
}

interface ErrorEnvelope {
  error?: {
    code?: string
    message?: string
    details?: ApiErrorDetail[]
  }
}

/** Thrown by `unwrap` for any non-2xx response. Screens catch this in their busy/try-catch. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details?: ApiErrorDetail[]

  constructor(status: number, body: unknown) {
    const envelope = (body ?? {}) as ErrorEnvelope
    super(envelope.error?.message ?? `Error ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.code = envelope.error?.code ?? 'unknown_error'
    this.details = envelope.error?.details
  }
}

/**
 * Merge the bearer token into a `RequestInit`. Safe on public endpoints:
 * when there is no token it just returns the init unchanged.
 */
export function authInit(init: RequestInit = {}): RequestInit {
  const token = getToken()
  if (!token) return init
  return {
    ...init,
    headers: { ...(init.headers as Record<string, string>), Authorization: `Bearer ${token}` },
  }
}

/** The `{ data, status, headers }` shape every generated fetch function resolves to. */
interface FetchResponse {
  data: unknown
  status: number
}

/**
 * Await a generated client call and return its success `data`, throwing
 * `ApiError` on any >= 400 response. A 401 also clears the local session; a
 * 503 `db_waking` also raises the app-wide "database is waking" notice.
 * `S` is the success payload type the caller expects.
 */
export async function unwrap<S>(call: Promise<FetchResponse>): Promise<S> {
  const res = await call
  if (res.status >= 400) {
    if (res.status === 401) clearSession()
    const error = new ApiError(res.status, res.data)
    if (error.code === 'db_waking') notifyDbWaking()
    if (error.code === 'db_no_capacity') notifyDbWaking('no_capacity')
    throw error
  }
  return res.data as S
}
