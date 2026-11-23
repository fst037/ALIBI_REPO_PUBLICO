import { describe, expect, it } from 'vitest'
import { formatSql } from './sqlFormat'

// The SQL panel shows `formatSql(sql)` and "Copiar" copies it, so the formatted
// text has to be the same query: only whitespace may change.

/** The query with every run of whitespace outside string literals removed. */
function squeeze(sql: string): string {
  return sql.replace(/('(?:[^']|'')*')|\s+/g, (_, str: string | undefined) => str ?? '')
}

const QUERIES = [
  "SELECT sede, SUM(total) AS ventas FROM ventas WHERE fecha >= '2024-01-01' AND estado IN ('pagado', 'enviado') GROUP BY sede ORDER BY ventas DESC LIMIT 10",
  'SELECT T1.nombre, COUNT(*) FROM medicos AS T1 INNER JOIN turnos AS T2 ON T1.id = T2.medico_id GROUP BY T1.nombre',
  "SELECT a FROM (SELECT a FROM t WHERE b = 'x from y') AS s",
  "SELECT nombre || ' ' || apellido AS completo FROM clientes WHERE edad <= 30 AND estado <> 'baja' AND id != 4",
  'SELECT CAST(monto AS DOUBLE), monto::INTEGER FROM pagos',
  'SELECT region, SUM(amount) FILTER (WHERE month < 4) AS q1 FROM sales GROUP BY region',
]

describe('formatSql', () => {
  it.each(QUERIES)('changes only whitespace: %s', (sql) => {
    expect(squeeze(formatSql(sql))).toBe(squeeze(sql))
  })

  it('keeps two-character operators whole', () => {
    const out = formatSql("SELECT a FROM t WHERE a >= 1 AND b <= 2 AND c <> 3 AND d != 4 AND e || f = 'x'")
    for (const op of ['>=', '<=', '<>', '!=', '||']) expect(out).toContain(` ${op} `)
    expect(out).not.toMatch(/[<>!|] [=>|]/)
  })

  it('puts each clause on its own line and the select list under SELECT', () => {
    expect(formatSql('SELECT sede, SUM(total) AS ventas FROM ventas GROUP BY sede ORDER BY ventas DESC')).toBe(
      ['SELECT', '  sede,', '  SUM(total) AS ventas', 'FROM ventas', 'GROUP BY sede', 'ORDER BY ventas DESC'].join('\n'),
    )
  })

  it('indents a subquery and leaves function calls and IN lists inline', () => {
    const out = formatSql("SELECT a FROM (SELECT a FROM t WHERE b IN (1, 2)) AS s WHERE COALESCE(a, 0) > 1")
    expect(out).toContain('FROM (\n  SELECT\n    a\n  FROM t\n  WHERE b IN (1, 2)\n) AS s')
    expect(out).toContain('COALESCE(a, 0) > 1')
  })

  it('does not break inside a string literal that contains keywords', () => {
    expect(formatSql("SELECT a FROM t WHERE b = 'x from y group by z'")).toContain("'x from y group by z'")
  })

  it('returns empty or blank input unchanged', () => {
    expect(formatSql('')).toBe('')
    expect(formatSql('   ')).toBe('   ')
  })
})
