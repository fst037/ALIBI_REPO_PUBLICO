import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import { Skeleton } from './Skeleton'

export interface Column<Row> {
  key: string
  header: ReactNode
  /** `num` right-aligns and uses the mono/tnum treatment. */
  align?: 'left' | 'num'
  render?: (row: Row) => ReactNode
}

export interface DataTableProps<Row extends Record<string, unknown>> {
  columns: Column<Row>[]
  rows: Row[]
  rowKey?: (row: Row, index: number) => string | number
  className?: string
  /** Wrapper style, e.g. max-width. */
  style?: React.CSSProperties
}

/** Paginated results table (`table.data`) with sticky headers and numeric columns. */
export function DataTable<Row extends Record<string, unknown>>({
  columns,
  rows,
  rowKey,
  className,
  style,
}: DataTableProps<Row>) {
  return (
    <div className={cx('table-wrap', className)} style={style}>
      <table className="data">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key} className={col.align === 'num' ? 'num' : undefined}>
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={rowKey ? rowKey(row, i) : i}>
              {columns.map((col) => (
                <td key={col.key} className={col.align === 'num' ? 'num' : undefined}>
                  {col.render ? col.render(row) : (row[col.key] as ReactNode)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export interface TableSkeletonProps {
  /** Match the real table's column count so the layout doesn't jump on swap. */
  columns?: number
  rows?: number
  className?: string
}

/**
 * `DataTable`'s loading state — same wrapper, same row rhythm, so a preview
 * that's still fetching reads as "loading" rather than "empty". Widths are
 * varied per cell so the placeholder doesn't look like a suspiciously perfect
 * grid of identical bars.
 */
export function TableSkeleton({ columns = 4, rows = 5, className }: TableSkeletonProps) {
  return (
    <div className={cx('table-wrap', className)}>
      <table className="data">
        <thead>
          <tr>
            {Array.from({ length: columns }).map((_, i) => (
              <th key={i}><Skeleton width="55%" /></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }).map((_, r) => (
            <tr key={r}>
              {Array.from({ length: columns }).map((_, c) => (
                <td key={c}><Skeleton width={`${45 + ((r + c) % 3) * 18}%`} /></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
