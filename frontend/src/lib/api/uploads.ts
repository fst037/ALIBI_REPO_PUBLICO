import type { Upload } from '../../types'
import * as gen from './generated/uploads/uploads'
import { ApiError, authInit, unwrap } from './http'
import { getToken } from './session'

/* ============================================================
   Uploads (Module B ingestion).

   The API never receives the file. `POST /uploads` returns a presigned URL and
   the browser PUTs the bytes straight to the object store — which is why the
   progress bar here is a *real* measurement (`XMLHttpRequest.upload.onprogress`)
   rather than an animation: there is an actual transfer to measure.

   `fetch` is not used for the PUT. It has no upload-progress event at all —
   the streaming request body that would give you one is not supported for
   uploads in most browsers — and progress on a multi-megabyte file is the
   whole point of this screen. XHR is the right tool, not a legacy one.
   ============================================================ */

/** Formats the API accepts. Mirrors `DatasetFileType` plus the aliases the
 *  backend's extension map understands. */
export const ACCEPTED_EXTENSIONS = [
  '.csv', '.tsv', '.txt', '.xlsx', '.xlsm', '.xls', '.pdf', '.docx', '.json', '.jsonl', '.ndjson',
]
export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.join(',')

export interface StartUploadOptions {
  /** Attach the resulting datasets to this folder. */
  folderId?: string
  /** 0–100, from the real transfer. */
  onProgress?: (pct: number) => void
  signal?: AbortSignal
}

/**
 * PUT the bytes to wherever the presign pointed.
 *
 * The bearer token is attached only for a **relative** URL — that means the
 * local-development receiver, which is our own API and has no signature to
 * rely on. A presigned S3 URL carries its own SigV4 credentials in the query
 * string, and adding an `Authorization` header to it makes S3 reject the
 * request, so the two cases genuinely differ.
 */
function putBytes(
  url: string,
  file: File,
  headers: Record<string, string>,
  options: StartUploadOptions,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url, true)

    for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value)
    const isSameOrigin = url.startsWith('/')
    const token = getToken()
    if (isSameOrigin && token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        options.onProgress?.(Math.round((event.loaded / event.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        options.onProgress?.(100)
        resolve()
        return
      }
      let body: unknown = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        // A store that isn't our API answers with XML or nothing at all.
      }
      reject(new ApiError(xhr.status, body))
    }
    xhr.onerror = () => reject(new Error('No pudimos subir el archivo. Revisá tu conexión.'))
    xhr.onabort = () => reject(new DOMException('Subida cancelada', 'AbortError'))

    options.signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.send(file)
  })
}

/**
 * Register the upload, then transfer the file. Resolves once the bytes have
 * landed — processing continues server-side, so follow this with `pollUpload`.
 */
export async function startUpload(file: File, options: StartUploadOptions = {}): Promise<Upload> {
  const created = await unwrap<{
    upload: Upload
    uploadUrl: string
    headers?: Record<string, string>
    method?: string
  }>(
    gen.createUpload(
      {
        filename: file.name,
        sizeBytes: file.size,
        contentType: file.type || undefined,
        folderId: options.folderId,
      },
      authInit(),
    ),
  )

  await putBytes(created.uploadUrl, file, created.headers ?? {}, options)
  return created.upload
}

export function getUpload(id: string): Promise<Upload> {
  return unwrap<Upload>(gen.getUpload(id, authInit()))
}

export function listUploads(): Promise<Upload[]> {
  return unwrap<Upload[]>(gen.listUploads(authInit()))
}

/** Retry a failed upload. Throws `ApiError` (409) if the raw file has expired. */
export function retryUpload(id: string): Promise<Upload> {
  return unwrap<Upload>(gen.retryUpload(id, authInit()))
}

/** Cancel an upload, discarding datasets that were never confirmed. */
export async function deleteUpload(id: string): Promise<void> {
  await unwrap<void>(gen.deleteUpload(id, authInit()))
}

const POLL_INTERVAL_MS = 800

/**
 * How long to keep polling before calling it a failure.
 *
 * There must be a ceiling. `status` only leaves `processing` because the worker
 * writes `ready` or `error`; if the worker dies without recording anything — a
 * crash, an OOM, a Lambda timeout — nothing ever writes that row again and an
 * unbounded loop spins forever, which is exactly the "stuck on the ingest
 * screen" report this replaces. A stall is indistinguishable from slowness from
 * out here, so the honest move is to stop waiting and say so.
 *
 * Six minutes is far above any legitimate run. It was three while the hard
 * upload limit was 25 MB (seconds of pipeline); Pro now uploads up to 100 MB,
 *so the ceiling scales with it.
 */
const POLL_TIMEOUT_MS = 6 * 60 * 1000

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** Thrown when the pipeline stops reporting progress. Carries the last state seen. */
export class UploadStalled extends Error {
  readonly upload: Upload
  constructor(upload: Upload) {
    super(
      'No pudimos terminar de procesar este archivo. Puede ser demasiado complejo o el ' +
        'proceso pudo haberse interrumpido. Probá subirlo de nuevo.',
    )
    this.name = 'UploadStalled'
    this.upload = upload
  }
}

/**
 * Poll until the upload reaches `ready` or `error`, or until it stalls.
 *
 * Polling rather than a websocket: the API is a Lambda behind an HTTP API,
 * which cannot hold a connection open per upload, and the stepper only has five
 * states to show. `onTick` fires on every response so the UI can advance the
 * phase indicator as it goes.
 */
export async function pollUpload(
  id: string,
  onTick?: (upload: Upload) => void,
  signal?: AbortSignal,
): Promise<Upload> {
  const deadline = Date.now() + POLL_TIMEOUT_MS
  for (;;) {
    if (signal?.aborted) throw new DOMException('Cancelado', 'AbortError')
    const upload = await getUpload(id)
    onTick?.(upload)
    if (upload.status === 'ready' || upload.status === 'error') return upload
    if (Date.now() >= deadline) throw new UploadStalled(upload)
    await sleep(POLL_INTERVAL_MS)
  }
}
