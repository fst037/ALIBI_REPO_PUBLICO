import type { Column } from '../../components/ui'
import { columnDecimals, formatCell } from '../../lib/format'
import type { PreviewColumn, ResultColumn, ResultTable, TablePreview } from '../../types'

export type ResultRow = Record<string, string | number>
/** An ingestion preview can hold nulls where a query result cannot. */
export type PreviewRow = Record<string, string | number | null>

/**
 * Map a table definition to DataTable columns.
 *
 * Accepts a query result *or* an ingestion preview: `PreviewColumn` and
 * `ResultColumn` are the same shape on purpose, so the confirmation screen and
 * the answer card render through one component and one adapter.
 *
 * Numeric columns get a renderer because an ingestion preview carries real
 * numbers straight out of Parquet — `1234.56`, `980`, `75.5` — which have to
 * read as `1,234.56`. Query-result fixtures hold pre-formatted strings, which
 * `formatCell` deliberately leaves alone.
 */
export function toColumns<T extends ResultRow | PreviewRow>(table: {
  columns: (ResultColumn | PreviewColumn)[]
  rows: T[]
}): Column<T>[] {
  return table.columns.map((c) => {
    if (c.align !== 'num') return { key: c.key, header: c.label, align: c.align }
    // Decided once per column from all its values, so the column doesn't mix
    // `1.234,56` with `980`.
    const decimals = columnDecimals(table.rows.map((row) => row[c.key]))
    return {
      key: c.key,
      header: c.label,
      align: c.align,
      render: (row: T) => formatCell(row[c.key], decimals),
    }
  })
}

export type AnyTable = ResultTable | TablePreview
