import { useState } from 'react'
import { Broom, Warning } from '@phosphor-icons/react'
import { Disclosure, Icon, Pill, Segmented, Spinner, Switch } from '../../components/ui'
import { cx } from '../../lib/cx'
import type {
  CleaningPreview,
  ColumnCleaning,
  ColumnKind,
  ColumnQuality,
  DatasetQuality,
  DuplicateStrategy,
  NullStrategy,
  SchemaColumn,
  TableCleaning,
  TextCase,
} from '../../types'
import { KIND_LABELS, NULL_LABELS, isNoopColumn, isNoopTable, nullOptions } from './etlite'
import styles from './EtlitePanel.module.css'

export interface EtlitePanelProps {
  columns: SchemaColumn[]
  /** undefined while it loads. */
  quality: DatasetQuality | undefined
  table: TableCleaning | undefined
  onColumn: (ordinal: number, cleaning: ColumnCleaning | null) => void
  onTable: (cleaning: TableCleaning) => void
  /** The server's preview of the current recipe, null while none is active. */
  result: CleaningPreview | null
  error: string | null
  busy: boolean
  /**
   * What this cleaning is applied to. The screen can show the panel twice —
   * once for the new table, once inside "update an existing dataset" — and each
   * acts on a different table, so each says which.
   */
  intro?: string
}

const fmt = (n: number) => n.toLocaleString('en-US')

const DUPLICATES: { value: DuplicateStrategy; label: string }[] = [
  { value: 'keep', label: 'Mantener' },
  { value: 'exact', label: 'Quitar idénticas' },
  { value: 'by_columns', label: 'Según columnas' },
]

/**
 * ETLite — optional light cleaning on the schema confirmation screen (1.2).
 *
 * Collapsed by default and never required: the summary in the header says
 * whether there is anything worth opening it for. Until the user confirms,
 * every choice is only previewed; confirming writes the cleaned table as the
 * dataset — ALIBI never kept the uploaded document, the user has it.
 */
export function EtlitePanel({ columns, quality, table, onColumn, onTable, result, error, busy, intro }: EtlitePanelProps) {
  const [showAll, setShowAll] = useState(false)
  const byOrdinal = new Map((quality?.columns ?? []).map((q) => [q.ordinal, q]))

  const withEmpties = (quality?.columns ?? []).filter((q) => q.nullCount > 0 && columns[q.ordinal]?.included).length
  const duplicates = quality?.duplicateRows ?? 0
  const active = columns.filter((c) => c.included && !isNoopColumn(c.cleaning)).length + (isNoopTable(table) ? 0 : 1)
  const fills = new Map((result?.fills ?? []).map((f) => [f.ordinal, f.value]))

  const rows = columns
    .map((column, ordinal) => ({ column, ordinal, q: byOrdinal.get(ordinal) }))
    .filter(({ column, q }) => column.included && q)
    .filter(
      ({ column, q }) =>
        showAll || !isNoopColumn(column.cleaning) || (q?.nullCount ?? 0) > 0 || (q?.whitespaceCount ?? 0) > 0,
    )

  const summary =
    quality === undefined ? null : active > 0 ? (
      <Pill tone="accent">{active === 1 ? '1 paso activo' : `${active} pasos activos`}</Pill>
    ) : withEmpties + duplicates > 0 ? (
      <Pill tone="warn">
        {[
          withEmpties > 0 && `${withEmpties} ${withEmpties === 1 ? 'columna con vacíos' : 'columnas con vacíos'}`,
          duplicates > 0 && `${fmt(duplicates)} ${duplicates === 1 ? 'fila duplicada' : 'filas duplicadas'}`,
        ]
          .filter(Boolean)
          .join(' · ')}
      </Pill>
    ) : (
      <Pill tone="ok">Sin problemas a la vista</Pill>
    )

  const keys = table?.duplicateColumns ?? []
  const toggleKey = (ordinal: number) =>
    onTable({
      duplicates: 'by_columns',
      duplicateColumns: keys.includes(ordinal) ? keys.filter((k) => k !== ordinal) : [...keys, ordinal].sort((a, b) => a - b),
    })

  return (
    <div className={styles.wrap}>
      <Disclosure
        icon={Broom}
        label={<span>Limpieza de datos <span className={styles.opt}>· opcional</span></span>}
        actions={summary}
      >
        <div className={styles.body}>
          <p className={styles.intro}>
            {intro ? `${intro} ` : ''}No cambia la estructura de la tabla, solo sus valores y qué filas quedan. Lo
            que ves abajo es una vista previa: al confirmar, el dataset se guarda ya limpio. Tu archivo original no se
            toca.
          </p>

          {quality === undefined ? (
            <div className={styles.loading}><Spinner /> Revisando la tabla…</div>
          ) : (
            <>
              <section className={styles.section}>
                <div className={styles.secHead}>
                  <div>
                    <div className={styles.secTitle}>Filas duplicadas</div>
                    <div className="hint">
                      {duplicates > 0
                        ? `${fmt(duplicates)} ${duplicates === 1 ? 'fila repite' : 'filas repiten'} exactamente una anterior. Se conserva la primera.`
                        : 'No hay filas idénticas. También podés buscar duplicados según algunas columnas.'}
                    </div>
                  </div>
                  <Segmented
                    aria-label="Filas duplicadas"
                    options={DUPLICATES}
                    value={table?.duplicates ?? 'keep'}
                    onChange={(value) =>
                      onTable({ duplicates: value, duplicateColumns: value === 'by_columns' ? keys : [] })
                    }
                  />
                </div>
                {table?.duplicates === 'by_columns' && (
                  <div className={styles.keys}>
                    <span className="hint">Son duplicadas las filas que coinciden en:</span>
                    {columns.map((c, ordinal) =>
                      c.included ? (
                        <button
                          key={ordinal}
                          type="button"
                          className={cx('chip', styles.key, keys.includes(ordinal) && styles.keyOn)}
                          aria-pressed={keys.includes(ordinal)}
                          onClick={() => toggleKey(ordinal)}
                        >
                          {c.name}
                        </button>
                      ) : null,
                    )}
                  </div>
                )}
              </section>

              <section className={styles.section}>
                <div className={styles.secHead}>
                  <div className={styles.secTitle}>Columnas</div>
                  <Switch checked={showAll} onChange={setShowAll} label="Mostrar todas" showLabel />
                </div>
                {rows.length === 0 && (
                  <div className="hint">Ninguna columna tiene vacíos ni espacios de más.</div>
                )}
                {rows.map(({ column, ordinal, q }) => (
                  <ColumnRow
                    key={ordinal}
                    column={column}
                    quality={q as ColumnQuality}
                    total={quality.rowCount}
                    fill={fills.get(ordinal)}
                    onChange={(cleaning) => onColumn(ordinal, cleaning)}
                  />
                ))}
              </section>
            </>
          )}

          {error ? (
            <div className={styles.error}><Icon icon={Warning} size="sm" />{error}</div>
          ) : active > 0 && (result || busy) ? (
            <div className={styles.result}>
              {busy && <Spinner />}
              {result && (
                <span>
                  Después de limpiar: <span className="mono">{fmt(result.rowCount)}</span> filas
                  {result.removedRows > 0 && (
                    <span className={styles.removed}> ({fmt(result.removedRows)} eliminadas)</span>
                  )}
                </span>
              )}
            </div>
          ) : null}
        </div>
      </Disclosure>
    </div>
  )
}

interface ColumnRowProps {
  column: SchemaColumn
  quality: ColumnQuality
  total: number
  fill: string | undefined
  onChange: (cleaning: ColumnCleaning | null) => void
}

function ColumnRow({ column, quality, total, fill, onChange }: ColumnRowProps) {
  const cleaning = column.cleaning ?? {}
  const kind: ColumnKind = quality.kind
  const nulls = cleaning.nulls ?? 'keep'
  // Kept as typed and parsed on blur: splitting on every keystroke would eat
  // the trailing comma the user is about to type after.
  const [tokens, setTokens] = useState((cleaning.nullTokens ?? []).join(', '))

  const patch = (next: Partial<ColumnCleaning>) => {
    const merged: ColumnCleaning = { ...cleaning, ...next }
    onChange(isNoopColumn(merged) ? null : merged)
  }
  const commitTokens = () =>
    patch({ nullTokens: tokens.split(',').map((t) => t.trim()).filter(Boolean) })

  const pct = total > 0 ? Math.round((quality.nullCount / total) * 100) : 0
  const shownFill = nulls !== 'keep' && nulls !== 'drop_row' && nulls !== 'value' ? fill : undefined

  return (
    <div className={styles.col}>
      <div className={styles.colHead}>
        <span className={cx('mono', styles.colName)}>{column.name}</span>
        <span className={styles.kind}>{KIND_LABELS[kind]}</span>
        <span className="grow" />
        {quality.nullCount > 0 && (
          <span className={styles.issue}>{fmt(quality.nullCount)} vacías ({pct < 1 ? '<1' : pct}%)</span>
        )}
        {(quality.whitespaceCount ?? 0) > 0 && (
          <span className={styles.issue}>{fmt(quality.whitespaceCount ?? 0)} con espacios de más</span>
        )}
      </div>

      <div className={styles.controls}>
        <label className={styles.ctl}>
          <span className={styles.ctlLab}>Celdas vacías</span>
          <select
            className={styles.field}
            value={nulls}
            onChange={(e) => patch({ nulls: e.target.value as NullStrategy, fillValue: null })}
          >
            {nullOptions(kind).map((o) => <option key={o} value={o}>{NULL_LABELS[o]}</option>)}
          </select>
          {shownFill !== undefined && <span className={cx('mono', styles.fill)}>= {shownFill}</span>}
        </label>

        {nulls === 'value' && (
          <label className={styles.ctl}>
            <span className={styles.ctlLab}>Valor</span>
            {kind === 'boolean' ? (
              <select
                className={styles.field}
                value={cleaning.fillValue ?? ''}
                onChange={(e) => patch({ fillValue: e.target.value })}
              >
                <option value="" disabled>Elegí…</option>
                <option value="true">Verdadero</option>
                <option value="false">Falso</option>
              </select>
            ) : (
              <input
                className={styles.field}
                type={kind === 'date' ? 'date' : 'text'}
                inputMode={kind === 'integer' || kind === 'number' ? 'decimal' : undefined}
                value={cleaning.fillValue ?? ''}
                placeholder={kind === 'text' ? 'Sin dato' : '0'}
                onChange={(e) => patch({ fillValue: e.target.value })}
              />
            )}
          </label>
        )}

        {kind === 'text' && (
          <>
            <label className={styles.ctl}>
              <span className={styles.ctlLab}>Mayúsculas</span>
              <select
                className={styles.field}
                value={cleaning.case ?? 'keep'}
                onChange={(e) => patch({ case: e.target.value as TextCase })}
              >
                <option value="keep">Sin cambios</option>
                <option value="upper">TODO MAYÚSCULAS</option>
                <option value="lower">todo minúsculas</option>
              </select>
            </label>
            <label className={cx(styles.ctl, styles.wideCtl)}>
              <span className={styles.ctlLab}>Valores que significan “vacío”</span>
              <input
                className={styles.field}
                value={tokens}
                placeholder="desconocido, ?, sin info"
                onChange={(e) => setTokens(e.target.value)}
                onBlur={commitTokens}
                onKeyDown={(e) => e.key === 'Enter' && commitTokens()}
              />
            </label>
            <div className={styles.switchCtl}>
              <Switch
                checked={!!cleaning.trim}
                onChange={(trim) => patch({ trim })}
                label="Quitar espacios de más"
                showLabel
              />
            </div>
          </>
        )}

        {kind === 'number' && (
          <label className={styles.ctl}>
            <span className={styles.ctlLab}>Decimales</span>
            <select
              className={styles.field}
              value={cleaning.decimals ?? ''}
              onChange={(e) => patch({ decimals: e.target.value === '' ? null : Number(e.target.value) })}
            >
              <option value="">Sin redondear</option>
              {[0, 1, 2, 3, 4].map((d) => (
                <option key={d} value={d}>{d === 0 ? 'Redondear a entero' : `${d} decimal${d > 1 ? 'es' : ''}`}</option>
              ))}
            </select>
          </label>
        )}
      </div>
    </div>
  )
}
