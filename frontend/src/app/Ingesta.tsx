import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowClockwise,
  ArrowLeft,
  Check,
  CheckFat,
  CloudArrowUp,
  Info,
  Sparkle,
  Table,
  Warning,
  X,
} from '@phosphor-icons/react'
import { AlibiMark, Button, DataTable, FILE_ICON, Icon, Skeleton, Spinner, TableSkeleton } from '../components/ui'
import * as api from '../lib/api'
import { UploadStalled } from '../lib/api/uploads'
import { ApiError } from '../lib/api/http'
import { toColumns } from '../features/conversation/tableAdapter'
import { hasPendingUpload, takePendingUpload } from '../features/ingest/pendingUpload'
import { EtlitePanel } from '../features/ingest/EtlitePanel'
import { UpdateDatasetPanel } from '../features/ingest/UpdateDatasetPanel'
import { effectiveTable, hasCleaning } from '../features/ingest/etlite'
import type {
  CleaningPreview,
  ColumnCleaning,
  Dataset,
  DatasetFileType,
  DatasetQuality,
  DatasetSchema,
  InferenceConfidence,
  SchemaColumn,
  TableCleaning,
  TablePreview,
  Upload,
  UploadPhase,
} from '../types'
import { cx } from '../lib/cx'
import { usePlan } from '../features/billing/usePlan'
import styles from './Ingesta.module.css'

type View = 'idle' | 'uploading' | 'schema' | 'error' | 'done'

/**
 * The stepper. `inferring` and `storing` are shown as one step: with no model
 * configured the inference phase passes in milliseconds, and a step that only
 * ever flashes is noise rather than information.
 */
const PHASES: { label: string; phases: UploadPhase[] }[] = [
  { label: 'Subiendo el archivo', phases: ['uploading'] },
  { label: 'Analizando la estructura', phases: ['extracting'] },
  { label: 'Infiriendo los tipos de columna', phases: ['profiling'] },
  { label: 'Preparando la tabla', phases: ['inferring', 'storing', 'done'] },
]

const CONFIDENCE: Record<InferenceConfidence, { text: string; color: string }> = {
  high: { text: 'Alta', color: 'var(--ok)' },
  medium: { text: 'Media', color: 'var(--warn)' },
  low: { text: 'Baja', color: 'var(--danger)' },
}

const fmt = (n: number) => n.toLocaleString('en-US')

function extensionOf(name: string): DatasetFileType {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  if (['xlsx', 'xlsm', 'xls'].includes(ext)) return 'xlsx'
  if (ext === 'pdf') return 'pdf'
  if (ext === 'docx') return 'docx'
  if (['json', 'jsonl', 'ndjson'].includes(ext)) return 'json'
  return 'csv'
}

export function Ingesta() {
  const { plan } = usePlan()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  // Uploading from inside a folder attaches the results to it on arrival.
  const folderId = params.get('folder') ?? undefined
  // The Library links here for a dataset stuck `awaiting_confirmation` instead
  // of making the user re-upload the file — see the effect below.
  const resumeDatasetId = params.get('dataset') ?? undefined

  const [view, setView] = useState<View>('idle')
  const [drag, setDrag] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [pct, setPct] = useState(0)
  const [upload, setUpload] = useState<Upload | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const abort = useRef<AbortController | null>(null)

  // Per-table confirmation state, keyed by dataset id so switching between the
  // tables of one upload doesn't discard edits made to another.
  const [active, setActive] = useState(0)
  // The whole schema, not just its columns: `confidence` and `tableDescription`
  // are what tell the user whether the inference is worth a careful look.
  const [schemas, setSchemas] = useState<Record<string, DatasetSchema>>({})
  const [names, setNames] = useState<Record<string, string>>({})
  const [confirmed, setConfirmed] = useState<string[]>([])
  // Tables that went into an existing dataset instead of becoming one. They count
  // as handled (in `confirmed`) but the dataset that now holds them is the one to open.
  const [updated, setUpdated] = useState<{ stagedId: string; dataset: Dataset }[]>([])
  const [preview, setPreview] = useState<TablePreview | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  // Set when the API refused the name as already taken; cleared as soon as the
  // user types, so the block never outlives the problem.
  const [nameError, setNameError] = useState(false)
  const nameRef = useRef<HTMLInputElement>(null)

  // ETLite. Column recipes live on the schema's columns (they travel with the
  // confirm like a rename does); the table-level one is kept here, per table.
  const [tableCleaning, setTableCleaning] = useState<Record<string, TableCleaning>>({})
  // undefined while loading; null when the table can't be inspected, which
  // hides the panel rather than offering cleaning that couldn't run.
  const [quality, setQuality] = useState<DatasetQuality | null | undefined>(undefined)
  const [cleaned, setCleaned] = useState<CleaningPreview | null>(null)
  const [cleaningError, setCleaningError] = useState<string | null>(null)
  const [cleaningBusy, setCleaningBusy] = useState(false)

  useEffect(() => () => abort.current?.abort(), [])

  // Resuming confirmation for a dataset that already finished uploading and
  // is just sitting `awaiting_confirmation` — the Library links here rather
  // than making the user find and re-upload the original file. Reusing the
  // exact `schema` view a fresh upload lands on means wrapping the one
  // dataset in an `Upload`-shaped object: everything below only ever reads
  // `upload.datasets`/`.filename`/`.warnings`, so nothing downstream needs to
  // know this wasn't a real upload.
  useEffect(() => {
    if (!resumeDatasetId) return
    let alive = true
    void api.getDataset(resumeDatasetId).then((dataset) => {
      if (!alive) return
      if (!dataset) {
        navigate('/biblioteca')
        return
      }
      setUpload({
        id: '',
        filename: dataset.name,
        fileType: dataset.fileType,
        sizeBytes: 0,
        status: 'ready',
        createdAt: new Date().toISOString(),
        datasets: [dataset],
        warnings: [],
      })
      setActive(0)
      setSchemas({})
      setNames({})
      setTableCleaning({})
      setConfirmed([])
      setUpdated([])
      setView('schema')
    })
    return () => {
      alive = false
    }
    // Runs once off the id present in the URL at mount — not a live
    // subscription that should re-fire on every re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeDatasetId])

  const tables = upload?.datasets ?? []
  const current: Dataset | undefined = tables[active]
  const schema = current ? schemas[current.id] : undefined
  const columns = useMemo(() => schema?.columns ?? [], [schema])
  const table = current ? tableCleaning[current.id] : undefined
  const cleaningOn = hasCleaning(columns, table)

  /** Load the schema and a preview for whichever table is on screen. */
  useEffect(() => {
    if (!current || schemas[current.id]) return
    const id = current.id
    let alive = true
    void api.getSchema(id).then((loaded) => {
      if (alive && loaded) setSchemas((s) => ({ ...s, [id]: loaded }))
    })
    return () => {
      alive = false
    }
  }, [current, schemas])

  useEffect(() => {
    if (!current) return
    const id = current.id
    setPreview(null)
    // A save failure belongs to the table it happened on.
    setSaveError(null)
    setNameError(false)
    let alive = true
    void api.previewDataset(id, 8).then((p) => alive && setPreview(p))
    setQuality(undefined)
    void api
      .getDatasetQuality(id)
      .then((q) => alive && setQuality(q))
      .catch(() => alive && setQuality(null))
    return () => {
      alive = false
    }
  }, [current])

  /**
   * The table as the current recipe would leave it, from the server — the
   * same check confirming runs, so the preview cannot promise what the save
   * then refuses. Debounced: a typed fill value would otherwise be a request
   * per keystroke.
   */
  useEffect(() => {
    setCleaningError(null)
    if (!current || !cleaningOn) {
      setCleaned(null)
      setCleaningBusy(false)
      return
    }
    const id = current.id
    let alive = true
    setCleaningBusy(true)
    const timer = setTimeout(() => {
      api
        .previewCleaning(id, columns, effectiveTable(table, columns))
        .then((result) => {
          if (!alive) return
          setCleaned(result)
          setCleaningError(null)
        })
        .catch((err: unknown) => {
          if (!alive) return
          setCleaned(null)
          setCleaningError(err instanceof ApiError ? err.message : 'No pudimos aplicar la limpieza.')
        })
        .finally(() => alive && setCleaningBusy(false))
    }, 400)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [current, columns, table, cleaningOn])

  const run = useCallback(
    async (chosen: File, existingId?: string) => {
      setFile(chosen)
      setFailure(null)
      setPct(0)
      setView('uploading')
      abort.current?.abort()
      const controller = new AbortController()
      abort.current = controller

      try {
        let started: Upload
        if (existingId) {
          // A retry re-reads bytes the store already holds, so there is no
          // transfer to measure. Jumping straight to 100% is what lets the
          // stepper advance past "Uploading file" instead of freezing there.
          started = await api.retryUpload(existingId)
          setPct(100)
        } else {
          started = await api.startUpload(chosen, {
            folderId,
            onProgress: setPct,
            signal: controller.signal,
          })
        }
        setUpload(started)

        const finished = await api.pollUpload(started.id, setUpload, controller.signal)
        if (finished.status === 'error') {
          setFailure(finished.error ?? 'No pudimos procesar el archivo.')
          setView('error')
          return
        }
        setActive(0)
        setSchemas({})
        setNames({})
        setTableCleaning({})
        setConfirmed([])
        setUpdated([])
        setView('schema')
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return
        // The message is already user-facing when it came from the API — the
        // backend phrases ingestion errors in plain language on purpose
        // (`app/ingest/errors.py`), so it is surfaced verbatim. `UploadStalled`
        // carries its own copy: the pipeline stopped reporting, so there is no
        // server-side message to show.
        setFailure(
          err instanceof ApiError || err instanceof UploadStalled
            ? err.message
            : 'No pudimos subir el archivo.',
        )
        setView('error')
      }
    },
    [folderId],
  )

  // A file dropped on the source picker's dropzone (/app) arrives parked in the
  // handoff slot, because a File cannot be put in the URL. Starting it here is
  // what makes that drop the upload, instead of a navigation that lands on an
  // idle screen asking for the same file again.
  //
  // Deferred by a tick deliberately: React 18 StrictMode mounts effects, tears
  // them down, and mounts them again, and the unmount cleanup above aborts
  // whatever `abort.current` holds — an upload started synchronously in the
  // first pass would be killed by that teardown and never restart, since the
  // slot is emptied by then. The timer is cancelled instead, and the second
  // pass starts the real one. In production the effect runs once and the timer
  // fires immediately.
  useEffect(() => {
    if (!hasPendingUpload()) return
    const timer = setTimeout(() => {
      const chosen = takePendingUpload()
      if (chosen) void run(chosen)
    }, 0)
    return () => clearTimeout(timer)
  }, [run])

  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const chosen = e.target.files?.[0]
    // Reset the input so choosing the same file twice still fires a change.
    e.target.value = ''
    if (chosen) void run(chosen)
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDrag(false)
    const chosen = e.dataTransfer.files?.[0]
    if (chosen) void run(chosen)
  }

  const reset = () => {
    abort.current?.abort()
    setView('idle')
    setFile(null)
    setUpload(null)
    setPct(0)
    setFailure(null)
  }

  const retry = () => {
    if (upload && upload.status === 'error' && file) void run(file, upload.id)
    else if (file) void run(file)
  }

  const patchCol = (i: number, patch: Partial<SchemaColumn>) => {
    if (!current) return
    const id = current.id
    setSchemas((all) => {
      const target = all[id]
      if (!target) return all
      return {
        ...all,
        [id]: { ...target, columns: target.columns.map((c, j) => (j === i ? { ...c, ...patch } : c)) },
      }
    })
  }

  const setColumnCleaning = (i: number, cleaning: ColumnCleaning | null) => patchCol(i, { cleaning })

  const confirmCurrent = async () => {
    if (!current) return
    setSaving(true)
    setSaveError(null)
    setNameError(false)
    try {
      await api.confirmSchema(
        current.id,
        columns,
        names[current.id]?.trim() || undefined,
        effectiveTable(table, columns),
      )
      const done = [...confirmed, current.id]
      setConfirmed(done)
      const next = tables.findIndex((t) => !done.includes(t.id))
      if (next === -1) setView('done')
      else setActive(next)
    } catch (err) {
      // Stay on the confirmation screen: the user's edits are still in state
      // and the action is retryable, so kicking them to the error view would
      // lose work for a transient failure.
      setSaveError(err instanceof ApiError ? err.message : 'No pudimos guardar el esquema.')
      // A name clash is fixed in one specific field, so point at it rather than
      // leaving a message at the bottom and making the user work out where to go.
      if (err instanceof ApiError && err.code === 'dataset_name_taken') {
        setNameError(true)
        nameRef.current?.focus()
        nameRef.current?.select()
      }
    } finally {
      setSaving(false)
    }
  }

  /** The current table went into an existing dataset: same hand-off as confirming it. */
  const tableUpdated = (dataset: Dataset) => {
    if (!current) return
    setUpdated((u) => [...u, { stagedId: current.id, dataset }])
    const done = [...confirmed, current.id]
    setConfirmed(done)
    const next = tables.findIndex((t) => !done.includes(t.id))
    if (next === -1) setView('done')
    else setActive(next)
  }

  const phaseIndex = useMemo(() => {
    if (view !== 'uploading') return 0
    if (!upload || pct < 100) return 0
    const found = PHASES.findIndex((p) => upload.phase && p.phases.includes(upload.phase))
    return found === -1 ? 1 : found
  }, [view, upload, pct])

  const activeCols = columns.filter((c) => c.included).length
  const confidence = schema ? CONFIDENCE[schema.confidence] : undefined
  const fileType = file ? extensionOf(file.name) : 'csv'

  return (
    <div className={styles.ing}>
      <div className={styles.top}>
        <Link className={styles.brand} to="/"><AlibiMark size={26} />ALIBI</Link>
        <Link className="btn btn-ghost btn-sm" to="/app"><Icon icon={X} size="sm" />Cancelar</Link>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={api.ACCEPT_ATTRIBUTE}
        onChange={pick}
        style={{ display: 'none' }}
      />

      <div className={styles.body}>
        {view === 'idle' && (
          <div className={cx(styles.inner, 'fade-up')}>
            <h1 className={styles.h}>Subir un archivo</h1>
            <p className={styles.sub}>Traé tus datos y en segundos vas a poder preguntarles lo que quieras.</p>
            <button
              type="button"
              className={cx(styles.bigDrop, drag && styles.drag)}
              onClick={() => inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault()
                if (!drag) setDrag(true)
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={onDrop}
            >
              <div className={styles.bigDropIco}><Icon icon={CloudArrowUp} size={32} /></div>
              <div>
                <div style={{ fontSize: 16, fontWeight: 600 }}>Soltá tu archivo acá</div>
                <div className="hint" style={{ marginTop: 5 }}>o hacé clic para elegir uno de tu computadora</div>
              </div>
              <div className={styles.dropFormats}>
                <span className="pill">CSV</span><span className="pill">XLSX</span>
                <span className="pill">PDF</span><span className="pill">DOCX</span><span className="pill">JSON</span>
              </div>
            </button>
            <div className={styles.limitNote}>
              {plan && (
                <>
                  <Icon icon={Info} size="sm" />Plan {plan.name}: hasta {plan.limits.maxFileMb} MB por archivo
                  {plan.limits.datasets != null && ` · ${plan.limits.datasets} datasets`}
                  {plan.id === 'free' && (
                    <>
                      {' · '}
                      <Link to="/cuenta?tab=billing" style={{ color: 'var(--accent)' }}>conseguí más con Pro</Link>
                    </>
                  )}
                </>
              )}
            </div>
          </div>
        )}

        {view === 'uploading' && (
          <div className={cx(styles.inner, 'fade-up')}>
            <div className={cx('glass', styles.upCard)}>
              <div className={styles.upFile}>
                <div className={styles.upFileIco}><Icon icon={FILE_ICON[fileType]} size={22} /></div>
                <div className="grow">
                  <div style={{ fontWeight: 600, fontSize: 14.5 }}>{file?.name}</div>
                  <div className="hint">{upload?.sizeLabel ?? ''}</div>
                </div>
                {/* Real transfer percentage while the bytes move; after that the
                    phase stepper is the honest progress indicator. */}
                {pct < 100
                  ? <span className="mono" style={{ fontSize: 13, color: 'var(--accent)' }}>{pct}%</span>
                  : <Spinner />}
              </div>
              <div className="usage-track"><div className="usage-fill" style={{ width: `${pct}%` }} /></div>
              <div className={styles.upPhases}>
                {PHASES.map((p, i) => {
                  const done = phaseIndex > i || (i === 0 && pct >= 100)
                  const isActive = phaseIndex === i && !done
                  return (
                    <div key={p.label} className={cx(styles.upPhase, done && styles.done, isActive && styles.active)}>
                      <span className={styles.phaseIco}>
                        {done ? <Icon icon={Check} size={13} /> : isActive ? <Spinner /> : null}
                      </span>
                      {p.label}
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        )}

        {view === 'schema' && current && (
          <div className={cx(styles.inner, styles.wide, 'fade-up')}>
            <h1 className={styles.h}>Confirmá el esquema</h1>
            <p className={styles.sub}>
              Esto es lo que detectamos en{' '}
              <span className="mono" style={{ color: 'var(--accent)' }}>{upload?.filename}</span>. Ajustá lo que
              necesites antes de continuar.
            </p>

            {(upload?.warnings ?? []).length > 0 && (
              <div className={styles.warnBox}>
                {upload?.warnings?.map((w) => (
                  <div key={w} className={styles.warnRow}><Icon icon={Warning} size="sm" />{w}</div>
                ))}
              </div>
            )}

            {/* The next month of a report: append to / replace a dataset the user already
                has, keeping its id so its charts and boards stay alive. Keyed by the
                table so switching tabs starts from that table's own candidates. */}
            <UpdateDatasetPanel key={`update-${current.id}`} staged={current} onUpdated={tableUpdated} />

            {/* One upload can hold several tables. Each is confirmed on its own,
                because each becomes its own dataset. */}
            {tables.length > 1 && (
              <div className={styles.tableTabs} role="tablist">
                {tables.map((t, i) => (
                  <button
                    key={t.id}
                    type="button"
                    role="tab"
                    aria-selected={i === active}
                    className={cx(styles.tableTab, i === active && styles.tabOn, confirmed.includes(t.id) && styles.tabDone)}
                    onClick={() => setActive(i)}
                  >
                    <Icon icon={confirmed.includes(t.id) ? Check : Table} size="sm" />
                    <span>{names[t.id] || t.name}</span>
                    <span className={styles.tabMeta}>{t.sourceLabel}</span>
                  </button>
                ))}
              </div>
            )}

            <div className={styles.nameRow}>
              <label className="sb-lab" htmlFor="ds-name">Nombre del dataset</label>
              <input
                id="ds-name"
                ref={nameRef}
                className={cx(styles.miniField, nameError && 'field-error')}
                value={names[current.id] ?? current.name}
                onChange={(e) => {
                  setNames((n) => ({ ...n, [current.id]: e.target.value }))
                  setNameError(false)
                  setSaveError(null)
                }}
                placeholder={current.name}
                aria-invalid={nameError || undefined}
              />
              {current.sourceLabel && <span className="hint">{current.sourceLabel}</span>}
            </div>

            <div className={styles.schSummary}>
              <div className={styles.schStat}>
                <div className={cx(styles.statV, 'mono')}>{fmt(schema?.rowCount ?? current.rows ?? 0)}</div>
                <div className={styles.statL}>Filas detectadas</div>
              </div>
              <div className={styles.schStat}>
                <div className={cx(styles.statV, 'mono')}>{activeCols}</div>
                <div className={styles.statL}>Columnas a incluir</div>
              </div>
              {cleaningOn && cleaned && (
                <div className={styles.schStat}>
                  <div className={cx(styles.statV, 'mono')} style={{ color: 'var(--accent)' }}>{fmt(cleaned.rowCount)}</div>
                  <div className={styles.statL}>Filas después de limpiar</div>
                </div>
              )}
              <div className={styles.schStat}>
                <div className={styles.statV} style={{ color: confidence?.color }}>{confidence?.text}</div>
                <div className={styles.statL}>Confianza de la inferencia</div>
              </div>
            </div>

            <div className={styles.schTable}>
              <div className={styles.schHead}><div /><div>Nombre de columna</div><div>Tipo</div><div>Ejemplos</div></div>
              {columns.length === 0 &&
                Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className={styles.schRow}>
                    <Skeleton variant="block" width={20} height={20} style={{ borderRadius: 6 }} />
                    <Skeleton width={`${60 + (i % 3) * 10}%`} />
                    <Skeleton width="55%" />
                    <Skeleton width={`${50 + (i % 4) * 10}%`} />
                  </div>
                ))}
              {columns.map((c, i) => (
                <div key={i} className={cx(styles.schRow, !c.included && styles.off)}>
                  <button
                    type="button"
                    className={cx(styles.incToggle, c.included && styles.on)}
                    onClick={() => patchCol(i, { included: !c.included })}
                    aria-label={c.included ? 'Excluir columna' : 'Incluir columna'}
                    title={c.included ? 'Excluir de las consultas' : 'Incluir en las consultas'}
                  >
                    <Icon icon={Check} size={12} />
                  </button>
                  <div>
                    <input className={styles.miniField} value={c.name} onChange={(e) => patchCol(i, { name: e.target.value })} />
                    {/* Only present when inference renamed it, so the user can
                        still recognise the column from the original file. */}
                    {c.sourceName && <div className={styles.srcName}>en el archivo: <span className="mono">{c.sourceName}</span></div>}
                  </div>
                  <select
                    className={styles.miniField}
                    value={c.type}
                    onChange={(e) => patchCol(i, { type: e.target.value as SchemaColumn['type'] })}
                  >
                    {api.COLUMN_TYPES.map((t) => <option key={t} value={t}>{api.COLUMN_TYPE_LABELS[t]}</option>)}
                  </select>
                  <div className={styles.schSample} title={c.description ?? undefined}>
                    {c.description || c.sampleValues}
                  </div>
                </div>
              ))}
            </div>

            {columns.length > 0 && quality !== null && (
              <EtlitePanel
                key={`clean-${current.id}`}
                columns={columns}
                quality={quality}
                table={table}
                onColumn={setColumnCleaning}
                onTable={(cleaning) => setTableCleaning((all) => ({ ...all, [current.id]: cleaning }))}
                result={cleaned}
                error={cleaningError}
                busy={cleaningBusy}
                intro="Se aplica a este archivo si lo confirmás como dataset nuevo; si lo sumás a uno existente, se elige en el bloque de arriba."
              />
            )}

            {/* With a recipe active, the rows shown are the cleaned ones — what
                the user is about to confirm, not what the file held. */}
            {(() => {
              const shown = cleaningOn && cleaned ? cleaned.preview : preview
              const label = cleaningOn && cleaned ? 'Primeras filas, ya limpias' : 'Primeras filas de la tabla'
              if (shown === null)
                return (
                  <div className={styles.previewWrap}>
                    <div className="sb-lab" style={{ padding: '0 0 8px' }}>{label}</div>
                    <TableSkeleton columns={columns.length || 4} rows={6} />
                  </div>
                )
              return shown.rows.length > 0 ? (
                <div className={styles.previewWrap}>
                  <div className="sb-lab" style={{ padding: '0 0 8px' }}>{label}</div>
                  <DataTable columns={toColumns(shown)} rows={shown.rows} />
                </div>
              ) : null
            })()}

            {saveError && (
              <div className={styles.warnBox}>
                <div className={styles.warnRow} style={{ color: 'var(--danger)' }}>
                  <Icon icon={Warning} size="sm" />{saveError}
                </div>
              </div>
            )}

            <div className={styles.schFoot}>
              <Button variant="glass" onClick={reset} disabled={saving}><Icon icon={ArrowLeft} size="sm" />Subir otro</Button>
              <Button
                variant="primary"
                onClick={confirmCurrent}
                disabled={saving || nameError || columns.length === 0 || (cleaningOn && (cleaningBusy || !!cleaningError))}
              >
                {saving ? <Spinner /> : <Icon icon={Check} size="sm" />}
                {saving
                  ? cleaningOn ? 'Guardando y limpiando la tabla…' : 'Guardando…'
                  : cleaningOn && cleaningBusy
                    ? 'Calculando la limpieza…'
                    : tables.length > 1
                      ? `Confirmar tabla ${active + 1} de ${tables.length}`
                      : 'Confirmar y usar este dataset'}
              </Button>
            </div>
          </div>
        )}

        {view === 'error' && (
          <div className={cx(styles.inner, 'fade-up')}>
            <div className={cx('glass', styles.errCard)}>
              <div className={styles.errIco}><Icon icon={Warning} size={28} /></div>
              <h1 className={styles.h} style={{ fontSize: 22 }}>No pudimos procesar el archivo</h1>
              <p className={styles.sub} style={{ marginBottom: 22 }}>{failure}</p>
              <div className="row" style={{ gap: 10, justifyContent: 'center' }}>
                <Button variant="glass" onClick={reset}>Elegir otro archivo</Button>
                <Button variant="primary" onClick={retry} disabled={!file}>
                  <Icon icon={ArrowClockwise} size="sm" />Reintentar
                </Button>
              </div>
            </div>
          </div>
        )}

        {view === 'done' && (() => {
          // What the user can now ask about: the datasets that were confirmed, and
          // the existing ones that received a table (not the staged table that vanished).
          const stagedIds = new Set(updated.map((u) => u.stagedId))
          const openable = [
            ...confirmed.filter((id) => !stagedIds.has(id)),
            ...new Set(updated.map((u) => u.dataset.id)),
          ]
          const onlyUpdates = updated.length > 0 && updated.length === confirmed.length
          return (
            <div className={cx(styles.inner, 'fade-up')} style={{ textAlign: 'center' }}>
              <div className={styles.doneIco}><Icon icon={CheckFat} size={32} /></div>
              <h1 className={styles.h}>
                {onlyUpdates
                  ? updated.length > 1 ? `${updated.length} datasets actualizados` : 'Dataset actualizado'
                  : tables.length > 1 ? `${tables.length} datasets listos` : 'Dataset listo'}
              </h1>
              <p className={styles.sub}>
                <span className="mono" style={{ color: 'var(--accent)' }}>{upload?.filename}</span>{' '}
                {onlyUpdates
                  ? `actualizó ${updated.map((u) => `«${u.dataset.name}»`).join(', ')}. Tus gráficos y tableros ya leen los datos nuevos.`
                  : tables.length > 1
                    ? 'generó estas tablas y ya podés consultarlas.'
                    : 'está procesado y listo para consultar.'}
              </p>
              <div className="row" style={{ gap: 10, justifyContent: 'center' }}>
                <Link className="btn btn-glass" to="/biblioteca">Ver en la biblioteca</Link>
                <Link className="btn btn-primary" to={openable.length === 1 ? `/app?dataset=${openable[0]}` : '/app'}>
                  <Icon icon={Sparkle} size="sm" />Empezar a preguntar
                </Link>
              </div>
            </div>
          )
        })()}
      </div>
    </div>
  )
}
