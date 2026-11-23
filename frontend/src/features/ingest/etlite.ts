import type {
  ColumnCleaning,
  ColumnKind,
  NullStrategy,
  SchemaColumn,
  TableCleaning,
} from '../../types'

/**
 * ETLite's client-side rules: which operations a column offers and when a
 * recipe is empty. The server re-checks all of this (`services/etlite.py`) —
 * these exist so the panel never offers a choice that would only come back as
 * a 422.
 */

export const NULL_LABELS: Record<NullStrategy, string> = {
  keep: 'Dejarlas vacías',
  drop_row: 'Eliminar la fila',
  zero: 'Rellenar con 0',
  mean: 'Rellenar con la media',
  median: 'Rellenar con la mediana',
  mode: 'Rellenar con el valor más frecuente',
  value: 'Rellenar con un valor…',
}

const ALL: NullStrategy[] = ['keep', 'drop_row', 'zero', 'mean', 'median', 'mode', 'value']
const NON_NUMERIC: NullStrategy[] = ['keep', 'drop_row', 'mode', 'value']

export function nullOptions(kind: ColumnKind): NullStrategy[] {
  if (kind === 'integer' || kind === 'number') return ALL
  if (kind === 'other') return ['keep', 'drop_row']
  return NON_NUMERIC
}

export const KIND_LABELS: Record<ColumnKind, string> = {
  integer: 'Número entero',
  number: 'Número',
  text: 'Texto',
  date: 'Fecha',
  boolean: 'Verdadero/Falso',
  other: 'Otro',
}

export function isNoopColumn(c: ColumnCleaning | null | undefined): boolean {
  return (
    !c ||
    ((c.nulls ?? 'keep') === 'keep' &&
      !c.trim &&
      (c.case ?? 'keep') === 'keep' &&
      !(c.nullTokens ?? []).some((t) => t.trim()) &&
      (c.decimals ?? null) === null)
  )
}

export function isNoopTable(t: TableCleaning | null | undefined): boolean {
  return !t || (t.duplicates ?? 'keep') === 'keep'
}

/** Only what applies: an excluded column's recipe and its role as a key are dropped. */
export function effectiveTable(t: TableCleaning | undefined, columns: SchemaColumn[]): TableCleaning | undefined {
  if (isNoopTable(t)) return undefined
  return {
    duplicates: t?.duplicates,
    duplicateColumns: (t?.duplicateColumns ?? []).filter((o) => columns[o]?.included),
  }
}

export function hasCleaning(columns: SchemaColumn[], t: TableCleaning | undefined): boolean {
  return !isNoopTable(t) || columns.some((c) => c.included && !isNoopColumn(c.cleaning))
}
