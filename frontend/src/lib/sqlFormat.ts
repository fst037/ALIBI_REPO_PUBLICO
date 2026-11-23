/* ============================================================
   SQL pretty-printing for display.

   Module A returns one query on one long line. That is fine to execute and
   miserable to read: the SQL panel is the product's honesty about what it ran,
   and a 300-character line scrolled sideways is not something anyone audits.
   This lays the clauses out so the query can be read at a glance.

   Hand-rolled rather than pulling in `sql-formatter`: the runtime dependency
   list is eight packages and this needs to cover the shape of SQL one model
   emits against one folder, not every dialect. It is display-only — the string
   sent to the backend is never this one — so the failure mode is cosmetic, and
   `formatSql` falls back to the original text if anything goes wrong.
   ============================================================ */

const INDENT = '  '

/** Clause keywords that begin a new line at the current nesting level. */
const CLAUSES = new Set([
  'SELECT', 'FROM', 'WHERE', 'HAVING', 'LIMIT', 'OFFSET', 'WITH', 'VALUES',
  'UNION', 'INTERSECT', 'EXCEPT', 'RETURNING', 'WINDOW', 'QUALIFY',
])

/** Keywords that take a `BY` and are written as one unit. */
const PAIRED = new Set(['GROUP', 'ORDER', 'PARTITION'])

/** Modifiers that can precede JOIN; they start the JOIN's line. */
const JOIN_PREFIX = new Set(['INNER', 'LEFT', 'RIGHT', 'FULL', 'CROSS', 'OUTER', 'NATURAL'])

/**
 * Words that are keywords rather than callable names. Only used to decide
 * spacing before `(`: `SUM(x)` is a call and closes up, `IN (1, 2)` is a
 * keyword followed by a list and keeps its space.
 */
const RESERVED = new Set([
  ...CLAUSES, ...PAIRED, ...JOIN_PREFIX,
  'AND', 'OR', 'NOT', 'IN', 'ON', 'USING', 'BY', 'AS', 'IS', 'LIKE', 'ILIKE',
  'BETWEEN', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'DISTINCT', 'ALL', 'ANY',
  'EXISTS', 'OVER', 'ASC', 'DESC', 'NULL', 'NULLS', 'FIRST', 'LAST', 'FILTER',
  'WITHIN', 'JOIN', 'SET', 'THEN',
])

type Token = { text: string; kind: 'word' | 'string' | 'comment' | 'punct' | 'number' }

// Two-character operators are one token: split, `>=` was written `> =`, which
// is not SQL — and the answer's "Copiar" copies this text.
const TOKEN =
  /(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^']|'')*'|"(?:[^"]|"")*"|`[^`]*`)|(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_$]*)|(\s+)|(<>|<=|>=|!=|==|\|\||::|[^\s])/g

function tokenize(sql: string): Token[] {
  const tokens: Token[] = []
  let match: RegExpExecArray | null
  TOKEN.lastIndex = 0
  while ((match = TOKEN.exec(sql)) !== null) {
    const [, comment, str, num, word, space, punct] = match
    if (space) continue
    if (comment) tokens.push({ text: comment, kind: 'comment' })
    else if (str) tokens.push({ text: str, kind: 'string' })
    else if (num) tokens.push({ text: num, kind: 'number' })
    else if (word) tokens.push({ text: word, kind: 'word' })
    else if (punct) tokens.push({ text: punct, kind: 'punct' })
  }
  return tokens
}

const upper = (t: Token | undefined) => (t && t.kind === 'word' ? t.text.toUpperCase() : '')

/**
 * One nesting level: a top-level statement, or a parenthesised subquery.
 *
 * `parenDepth` is the raw bracket depth this level lives at. Line breaks only
 * happen at that exact depth, which is what keeps a function call's arguments
 * (`SUM(a, b)`) and an `IN (1, 2, 3)` list on one line while the select list of
 * an actual subquery gets broken up.
 */
type Level = { base: number; parenDepth: number; inSelectList: boolean }

function format(sql: string): string {
  const tokens = tokenize(sql)
  const lines: string[] = []
  let line = ''
  let parenDepth = 0
  const levels: Level[] = [{ base: 0, parenDepth: 0, inSelectList: false }]

  // Set by `.` so that a qualified name comes out as `o.id`, never `o . id`.
  let tightNext = false

  const level = () => levels[levels.length - 1]
  const breakTo = (indent: number) => {
    if (line.trim()) lines.push(line.trimEnd())
    line = INDENT.repeat(Math.max(0, indent))
    tightNext = false
  }
  const write = (text: string, tight = false) => {
    const needsSpace = line.trim().length > 0 && !tight && !tightNext && !line.endsWith('(')
    line += (needsSpace ? ' ' : '') + text
    tightNext = false
  }
  /** Breaks are only legal at the depth the current level was opened at. */
  const atLevelDepth = () => parenDepth === level().parenDepth

  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i]
    const word = upper(token)
    const next = tokens[i + 1]

    if (token.kind === 'punct') {
      if (token.text === '(') {
        // A subquery is a bracket whose first real token starts a query. Every
        // other bracket - function call, IN list, arithmetic grouping - stays
        // inline, which is the distinction that keeps SUM(a) from exploding.
        const opensQuery = upper(next) === 'SELECT' || upper(next) === 'WITH'
        // No space between a function name and its arguments — but a keyword
        // is not a function, so `IN (1, 2)` and `OVER (...)` keep theirs.
        const prev = tokens[i - 1]
        const isCall = prev?.kind === 'word' && !RESERVED.has(prev.text.toUpperCase())
        write('(', isCall && !opensQuery)
        parenDepth += 1
        if (opensQuery) {
          const base = level().base + 1
          levels.push({ base, parenDepth, inSelectList: false })
          breakTo(base)
        }
        continue
      }
      if (token.text === ')') {
        if (levels.length > 1 && level().parenDepth === parenDepth) {
          const closing = levels.pop() as Level
          breakTo(closing.base - 1)
        }
        parenDepth = Math.max(0, parenDepth - 1)
        write(')', true)
        continue
      }
      if (token.text === ',') {
        write(',', true)
        if (level().inSelectList && atLevelDepth()) breakTo(level().base + 1)
        continue
      }
      if (token.text === '.') {
        write('.', true)
        tightNext = true
        continue
      }
      write(token.text, ',;'.includes(token.text))
      continue
    }

    if (token.kind === 'comment') {
      breakTo(level().base)
      write(token.text)
      breakTo(level().base)
      continue
    }

    if (token.kind === 'word' && atLevelDepth()) {
      // JOIN and its modifiers: only the FIRST word of the run breaks, so
      // "LEFT OUTER JOIN" is one line rather than three.
      const afterPrefix = JOIN_PREFIX.has(upper(tokens[i - 1]))
      if (!afterPrefix && (word === 'JOIN' || (JOIN_PREFIX.has(word) && isJoinAhead(tokens, i)))) {
        breakTo(level().base)
        level().inSelectList = false
        write(token.text)
        continue
      }
      if (word === 'ON' || word === 'USING') {
        breakTo(level().base + 1)
        write(token.text)
        continue
      }
      if (word === 'AND' || word === 'OR') {
        breakTo(level().base + 1)
        write(token.text)
        continue
      }
      if (PAIRED.has(word) && upper(next) === 'BY') {
        breakTo(level().base)
        level().inSelectList = false
        write(`${token.text} ${next.text}`)
        i += 1
        continue
      }
      if (CLAUSES.has(word)) {
        breakTo(level().base)
        write(token.text)
        if (word === 'SELECT') {
          level().inSelectList = true
          // DISTINCT / ALL belong on the SELECT line, not with the columns.
          if (upper(next) === 'DISTINCT' || upper(next) === 'ALL') {
            write(next.text)
            i += 1
          }
          breakTo(level().base + 1)
        } else if (word === 'UNION' || word === 'INTERSECT' || word === 'EXCEPT') {
          level().inSelectList = false
          if (upper(next) === 'ALL' || upper(next) === 'DISTINCT') {
            write(next.text)
            i += 1
          }
          breakTo(level().base)
        } else {
          level().inSelectList = false
        }
        continue
      }
    }

    write(token.text)
  }

  if (line.trim()) lines.push(line.trimEnd())
  return lines.join('\n')
}

/** Whether a run of JOIN modifiers starting at `i` actually reaches a JOIN. */
function isJoinAhead(tokens: Token[], i: number): boolean {
  for (let j = i + 1; j < tokens.length; j += 1) {
    const word = upper(tokens[j])
    if (word === 'JOIN') return true
    if (!JOIN_PREFIX.has(word)) return false
  }
  return false
}

/**
 * Lay a one-line query out over several lines. Display only.
 *
 * Returns the input unchanged if it is empty or if formatting throws — a SQL
 * panel showing an unformatted query is a small loss, showing nothing is not.
 */
export function formatSql(sql: string): string {
  if (!sql || !sql.trim()) return sql
  try {
    const formatted = format(sql)
    return formatted.trim() ? formatted : sql
  } catch {
    return sql
  }
}
