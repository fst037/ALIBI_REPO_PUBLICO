import { useEffect, useMemo, useState } from 'react'
import { ArrowsClockwise, Warning } from '@phosphor-icons/react'
import { Button, Disclosure, Icon, Pill, Segmented, Skeleton, Spinner } from '../../components/ui'
import * as api from '../../lib/api'
import { ApiError } from '../../lib/api/http'
import type {
  CleaningPreview,
  CleaningRecipe,
  ColumnCleaning,
  Dataset,
  DatasetQuality,
  DatasetUpdatePreview,
  OverlapStrategy,
  SchemaColumn,
  StructureDifference,
  TableCleaning,
  UpdateMode,
  UpdateTarget,
} from '../../types'
import { EtlitePanel } from './EtlitePanel'
import { effectiveTable, isNoopColumn } from './etlite'
import styles from './UpdateDatasetPanel.module.css'

const fmt = (n: number) => n.toLocaleString('en-US')

/** Datasets listed before "ver más" — the page should not grow with the library. */
const VISIBLE_TARGETS = 4
/** When nothing fits, only the closest ones are worth explaining. */
const VISIBLE_MISFITS = 2

const MODES: { value: UpdateMode; label: string }[] = [
  { value: 'append', label: 'Agregar las filas' },
  { value: 'replace', label: 'Reemplazar los datos' },
]

const OVERLAP: { value: OverlapStrategy; label: string }[] = [
  { value: 'keep_all', label: 'Agregarlas igual' },
  { value: 'skip', label: 'Omitir las repetidas' },
]

/** One structural difference, in the words a person would use for it. */
function describeDifference(d: StructureDifference): string {
  if (d.kind === 'missing') return `Al archivo le falta la columna «${d.column}»`
  if (d.kind === 'extra') return `El archivo trae una columna de más: «${d.column}»`
  if (d.kind === 'ambiguous') return `«${d.column}» coincide con más de una columna`
  return `«${d.column}» pasó de ${d.expected} a ${d.found}`
}

/** The recipe in the form the API stores: only what applies, by ordinal. */
function recipeOf(columns: SchemaColumn[], table: TableCleaning | undefined): CleaningRecipe | null {
  const cols = columns.flatMap((c, ordinal) =>
    c.included && c.cleaning && !isNoopColumn(c.cleaning) ? [{ ordinal, cleaning: c.cleaning }] : [],
  )
  const t = effectiveTable(table, columns)
  return cols.length === 0 && !t ? null : { table: t ?? null, columns: cols }
}

export interface UpdateDatasetPanelProps {
  /** The unconfirmed table the upload produced. */
  staged: Dataset
  /** Called once the dataset has been updated; the staged table no longer exists. */
  onUpdated: (updated: Dataset) => void
}

/**
 * Append the uploaded table to a dataset the user already has, or replace its
 * data — the monthly report. The dataset keeps its id and column names, which is
 * what keeps its charts and boards working; a file with another structure is
 * refused here with the differences, before anything is written.
 *
 * Renders nothing when there is no dataset to offer, so a first upload never
 * sees it.
 */
export function UpdateDatasetPanel({ staged, onUpdated }: UpdateDatasetPanelProps) {
  const [targets, setTargets] = useState<UpdateTarget[] | null>(null)
  const [targetId, setTargetId] = useState<string | null>(null)
  const [mode, setMode] = useState<UpdateMode>('append')
  const [overlap, setOverlap] = useState<OverlapStrategy>('keep_all')
  const [showAllTargets, setShowAllTargets] = useState(false)

  // The target's columns carry the recipe, as they do on the confirmation screen.
  const [columns, setColumns] = useState<SchemaColumn[]>([])
  const [table, setTable] = useState<TableCleaning | undefined>(undefined)
  const [quality, setQuality] = useState<DatasetQuality | undefined>(undefined)
  const [ready, setReady] = useState(false)

  const [preview, setPreview] = useState<DatasetUpdatePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [armed, setArmed] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let alive = true
    void api
      .getUpdateTargets(staged.id)
      .then((found) => {
        if (!alive) return
        setTargets(found)
        setTargetId(found.find((t) => t.compatible)?.datasetId ?? null)
      })
      .catch(() => alive && setTargets([]))
    return () => {
      alive = false
    }
  }, [staged.id])

  // Picking a dataset: load its columns and the recipe it was last cleaned with,
  // which is offered again (and can be edited) every time.
  useEffect(() => {
    setReady(false)
    setPreview(null)
    setError(null)
    setArmed(false)
    if (!targetId) return
    let alive = true
    void Promise.all([
      api.getSchema(targetId),
      api.getDatasetQuality(targetId).catch(() => null),
      api.previewDatasetUpdate(targetId, { stagedDatasetId: staged.id, mode: 'append' }),
    ])
      .then(([schema, q, first]) => {
        if (!alive || !schema) return
        const stored = first.storedCleaning
        const byOrdinal = new Map((stored?.columns ?? []).map((c) => [c.ordinal, c.cleaning]))
        setColumns(schema.columns.map((c, i) => ({ ...c, cleaning: byOrdinal.get(i) ?? null })))
        setTable(stored?.table ?? undefined)
        setQuality(q ?? undefined)
        setReady(true)
      })
      .catch((err: unknown) => alive && setError(err instanceof ApiError ? err.message : 'No pudimos leer el dataset.'))
    return () => {
      alive = false
    }
  }, [targetId, staged.id])

  const recipe = useMemo(() => recipeOf(columns, table), [columns, table])

  // Debounced like the cleaning preview: a typed fill value is not a request per keystroke.
  useEffect(() => {
    if (!targetId || !ready) return
    let alive = true
    setBusy(true)
    setArmed(false)
    const timer = setTimeout(() => {
      api
        .previewDatasetUpdate(targetId, { stagedDatasetId: staged.id, mode, onOverlap: overlap, cleaning: recipe })
        .then((p) => {
          if (!alive) return
          setPreview(p)
          setError(null)
        })
        .catch((err: unknown) => {
          if (!alive) return
          setPreview(null)
          setError(err instanceof ApiError ? err.message : 'No pudimos calcular el resultado.')
        })
        .finally(() => alive && setBusy(false))
    }, 350)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [targetId, ready, mode, overlap, recipe, staged.id])

  // Never silent: while the candidates load the section is already on screen and says so.
  if (targets === null)
    return (
      <div className={styles.wrap}>
        <Disclosure
          key="searching"
          icon={ArrowsClockwise}
          defaultOpen
          label={<span>¿Es la continuación de un dataset que ya tenés? <span className={styles.opt}>· opcional</span></span>}
          actions={<Spinner />}
        >
          <div className={styles.body} role="status" aria-live="polite">
            <div className={styles.activity}>
              <Spinner /> Buscando entre tus datasets los que tienen las mismas columnas que este archivo…
            </div>
            <Skeleton width="70%" />
            <Skeleton width="55%" />
          </div>
        </Disclosure>
      </div>
    )
  if (targets.length === 0) return null
  const target = targets.find((t) => t.datasetId === targetId)
  const anyFits = targets.some((t) => t.compatible)
  // Only what is relevant: the datasets this file can update. Those that cannot
  // are shown (closest first, the server sorts them) only when none fits, so a
  // refused file still gets its reason.
  const pool = anyFits ? targets.filter((t) => t.compatible) : targets
  const limit = anyFits ? VISIBLE_TARGETS : VISIBLE_MISFITS
  const shown = showAllTargets ? pool : pool.slice(0, limit)
  const hidden = pool.length - shown.length

  const rowsKept = (preview?.existingRows ?? 0) + (preview?.incomingRows ?? 0) - (overlap === 'skip' ? (preview?.overlapRows ?? 0) : 0)
  const cleanedView: CleaningPreview | null =
    preview?.resultRows != null && recipe
      ? {
          rowCount: preview.resultRows,
          removedRows: Math.max((mode === 'append' ? rowsKept : (preview.incomingRows ?? 0)) - preview.resultRows, 0),
          fills: preview.fills,
          preview: { datasetId: targetId ?? '', columns: [], rows: [] },
        }
      : null

  const apply = async () => {
    if (!targetId) return
    if (mode === 'replace' && !armed) {
      setArmed(true)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const updated = await api.applyDatasetUpdate(targetId, {
        stagedDatasetId: staged.id,
        mode,
        onOverlap: overlap,
        cleaning: recipe,
      })
      onUpdated(updated)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'No pudimos actualizar el dataset.')
      setArmed(false)
    } finally {
      setSaving(false)
    }
  }

  const compatible = preview?.compatible !== false
  // What the app is doing right now, in words. Whatever keeps the button greyed out
  // has to be named here, or the user can only guess it is slow.
  const activity = saving
    ? `${mode === 'append' ? 'Agregando' : 'Reemplazando'} ${fmt(preview?.incomingRows ?? 0)} filas${target ? ` en «${target.name}»` : ''}… con tablas grandes puede tardar unos segundos. No cierres esta pantalla.`
    : target && !ready && !error
      ? `Leyendo las columnas de «${target.name}»…`
      : busy
        ? 'Calculando cómo quedaría la tabla…'
        : null
  const canApply = !!targetId && ready && !!preview && compatible && !busy && !saving && !error

  return (
    <div className={styles.wrap}>
      <Disclosure
        // A different key from the "searching" one: once the candidates are known the
        // panel opens only if one fits, and `defaultOpen` is read on mount.
        key="found"
        icon={ArrowsClockwise}
        defaultOpen={anyFits}
        label={<span>¿Es la continuación de un dataset que ya tenés? <span className={styles.opt}>· opcional</span></span>}
        actions={anyFits ? <Pill tone="accent">Misma estructura encontrada</Pill> : <Pill>Sin coincidencias</Pill>}
      >
        <fieldset className={styles.body} disabled={saving}>
          <p className={styles.intro}>
            Sumá estas filas a un dataset existente, o reemplazá sus datos por este archivo. El dataset conserva su
            identidad y sus columnas, así que los gráficos y tableros que ya armaste siguen funcionando.
          </p>

          <section className={styles.section} aria-label="Dataset a actualizar">
            <div className={styles.secTitle}>Dataset a actualizar</div>
            <div className={styles.targets} role="radiogroup">
              {shown.map((t) => (
                <label
                  key={t.datasetId}
                  className={[styles.target, t.datasetId === targetId ? styles.on : '', t.compatible ? '' : styles.off].join(' ')}
                >
                  <input
                    type="radio"
                    name={`update-target-${staged.id}`}
                    checked={t.datasetId === targetId}
                    disabled={!t.compatible}
                    onChange={() => setTargetId(t.datasetId)}
                  />
                  <span className={styles.targetName}>{t.name}</span>
                  {t.rows != null && <span className="hint mono">{fmt(t.rows)} filas</span>}
                  {!t.compatible && (
                    <span className={styles.why}>
                      {(t.differences ?? []).slice(0, 3).map((d) => describeDifference(d)).join(' · ')}
                      {(t.differences ?? []).length > 3 && ` · y ${(t.differences ?? []).length - 3} más`}
                    </span>
                  )}
                </label>
              ))}
            </div>
            {hidden > 0 && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setShowAllTargets(true)}
                style={{ alignSelf: 'flex-start' }}
              >
                {anyFits ? `Ver ${hidden} más` : `Ver ${hidden} más que no coinciden`}
              </button>
            )}
            {!anyFits && (
              <div className="hint">
                Ninguno tiene exactamente las mismas columnas que este archivo. Para actualizar un dataset, el archivo
                nuevo tiene que mantener la estructura.
              </div>
            )}
          </section>

          {target && (
            <>
              <section className={styles.section}>
                <div className={styles.secHead}>
                  <div className={styles.secTitle}>Qué hacer con el archivo</div>
                  <Segmented aria-label="Modo de actualización" options={MODES} value={mode} onChange={setMode} />
                </div>
                <div className="hint">
                  {mode === 'append'
                    ? 'Las filas nuevas se agregan después de las que ya tiene — por ejemplo, el mes siguiente.'
                    : 'Las filas actuales se descartan y el dataset pasa a tener solo las de este archivo — por ejemplo, un informe corregido que ya incluye los meses anteriores.'}
                </div>

                {mode === 'append' && (preview?.overlapRows ?? 0) > 0 && (
                  <div className={styles.overlap}>
                    <div className={styles.warnRow}>
                      <Icon icon={Warning} size="sm" />
                      {fmt(preview?.overlapRows ?? 0)} de las {fmt(preview?.incomingRows ?? 0)} filas del archivo ya están
                      en el dataset, idénticas. ¿Subiste este mes antes?
                    </div>
                    <div className={styles.overlapActions}>
                      <Segmented aria-label="Filas repetidas" options={OVERLAP} value={overlap} onChange={setOverlap} />
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setMode('replace')}>
                        Mejor reemplazar
                      </button>
                    </div>
                  </div>
                )}
              </section>

              {ready && (
                <EtlitePanel
                  key={targetId}
                  columns={columns}
                  quality={quality}
                  table={table}
                  onColumn={(i: number, cleaning: ColumnCleaning | null) =>
                    setColumns((all) => all.map((c, j) => (j === i ? { ...c, cleaning } : c)))
                  }
                  onTable={setTable}
                  result={cleanedView}
                  error={error}
                  busy={busy}
                  intro={`Se aplica a «${target.name}» al actualizarlo, no a este archivo como dataset nuevo.`}
                />
              )}

              {!preview && busy && !error && <Skeleton width="60%" />}

              {preview && compatible && (
                <div className={styles.summary}>
                  <div>
                    <span className="mono">{fmt(preview.existingRows ?? 0)}</span> filas hoy
                    {mode === 'append' ? ' + ' : ' → '}
                    <span className="mono">{fmt(preview.incomingRows ?? 0)}</span> del archivo
                  </div>
                  <div className={styles.result}>
                    Quedarían <span className="mono">{fmt(preview.resultRows ?? 0)}</span> filas
                  </div>
                </div>
              )}

              {preview && !compatible && (
                <div className={styles.warnRow} style={{ color: 'var(--danger)' }}>
                  <Icon icon={Warning} size="sm" />
                  {(preview.differences ?? []).map(describeDifference).join(' · ')}
                </div>
              )}

              {error && !recipe && (
                <div className={styles.warnRow} style={{ color: 'var(--danger)' }}>
                  <Icon icon={Warning} size="sm" />{error}
                </div>
              )}

              {mode === 'replace' && armed && (
                <div className={styles.warnRow}>
                  <Icon icon={Warning} size="sm" />
                  Se van a descartar {fmt(preview?.existingRows ?? 0)} filas de «{target.name}». No se puede deshacer; tu
                  archivo original no se toca. Volvé a hacer clic para confirmar.
                </div>
              )}

              <div className={styles.foot}>
                {/* Announced to screen readers too: the button below is disabled for as
                    long as this is showing, and this is the reason why. */}
                <div className={styles.activity} role="status" aria-live="polite">
                  {activity && <><Spinner />{activity}</>}
                </div>
                <Button variant={mode === 'replace' ? 'glass' : 'primary'} onClick={apply} disabled={!canApply}>
                  {saving ? <Spinner /> : <Icon icon={ArrowsClockwise} size="sm" />}
                  {saving
                    ? mode === 'append' ? 'Agregando…' : 'Reemplazando…'
                    : mode === 'append'
                    ? `Agregar a «${target.name}»`
                    : armed
                      ? 'Sí, reemplazar los datos'
                      : `Reemplazar los datos de «${target.name}»`}
                </Button>
              </div>
            </>
          )}
        </fieldset>
      </Disclosure>
    </div>
  )
}
