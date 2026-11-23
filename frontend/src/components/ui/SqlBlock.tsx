import type { ReactNode } from 'react'
import { useMemo } from 'react'
import { Code } from '@phosphor-icons/react'
import { formatSql } from '../../lib/sqlFormat'
import { Disclosure } from './Disclosure'

export interface SqlBlockProps {
  sql: string
  /** Collapsed by default; open on the first query of a session. */
  defaultOpen?: boolean
  /** Optional actions rendered in the header (Copy, Edit…). */
  actions?: ReactNode
}

const KEYWORDS = new Set([
  'SELECT', 'FROM', 'WHERE', 'GROUP', 'ORDER', 'BY', 'AS', 'ON', 'AND', 'OR',
  'JOIN', 'INNER', 'LEFT', 'RIGHT', 'OUTER', 'FULL', 'HAVING', 'LIMIT', 'OFFSET',
  'DESC', 'ASC', 'DISTINCT', 'IN', 'NOT', 'NULL', 'IS', 'LIKE', 'BETWEEN', 'CASE',
  'WHEN', 'THEN', 'ELSE', 'END', 'UNION', 'ALL', 'WITH', 'OVER', 'PARTITION',
])
const FUNCTIONS = new Set([
  'SUM', 'COUNT', 'AVG', 'MIN', 'MAX', 'ROUND', 'COALESCE', 'CAST', 'DATE',
  'EXTRACT', 'NOW', 'ABS', 'CEIL', 'FLOOR', 'LOWER', 'UPPER',
])

const TOKEN = /('[^']*'|"[^"]*")|([A-Za-z_][A-Za-z0-9_]*)|(\d+(?:\.\d+)?)|(\s+|[^\sA-Za-z0-9_'"]+)/g

/** Minimal, dependency-free SQL syntax highlighting into styled spans. */
function highlight(sql: string): ReactNode[] {
  const out: ReactNode[] = []
  let match: RegExpExecArray | null
  let i = 0
  TOKEN.lastIndex = 0
  while ((match = TOKEN.exec(sql)) !== null) {
    const [, str, word, num, rest] = match
    const key = i++
    if (str) {
      out.push(<span key={key} className="sql-str">{str}</span>)
    } else if (word) {
      const upper = word.toUpperCase()
      if (KEYWORDS.has(upper)) out.push(<span key={key} className="sql-kw">{word}</span>)
      else if (FUNCTIONS.has(upper)) out.push(<span key={key} className="sql-fn">{word}</span>)
      else out.push(word)
    } else if (num) {
      out.push(<span key={key} className="sql-num">{num}</span>)
    } else {
      out.push(rest)
    }
  }
  return out
}

/**
 * Collapsible generated-SQL panel (`.sql-block`), pretty-printed and highlighted.
 *
 * The model returns the query on one line. `.sql-body` scrolls horizontally, so
 * that version was readable only by dragging — which defeats the point of
 * showing the SQL at all. `formatSql` lays the clauses out; it is display-only
 * and never touches what gets executed.
 */
export function SqlBlock({ sql, defaultOpen = false, actions }: SqlBlockProps) {
  const pretty = useMemo(() => formatSql(sql), [sql])
  return (
    <Disclosure label="SQL generado" icon={Code} defaultOpen={defaultOpen} actions={actions} bodyClassName="sql-body">
      {highlight(pretty)}
    </Disclosure>
  )
}
