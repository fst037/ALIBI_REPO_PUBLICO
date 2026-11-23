import { useState } from 'react'
import { formatCompact } from './chartCore'

export interface HeatmapProps {
  rows: string[]
  cols: string[]
  /** values[rowIndex][colIndex] */
  values: number[][]
  /** 'column' colors each column against its own max (metrics of different scales). */
  normalize?: 'global' | 'column'
}

const CELL_W = 58
const CELL_H = 34
const GAP = 3
const LEFT = 90
const TOP = 26

/** Matrix heatmap — intensity via --data-1 opacity, values printed in mono. */
export function Heatmap({ rows, cols, values, normalize = 'global' }: HeatmapProps) {
  const [hover, setHover] = useState<{ r: number; c: number } | null>(null)
  const globalMax = Math.max(...values.flat(), 0) || 1
  const colMax = cols.map((_, c) => Math.max(...values.map((row) => row[c] ?? 0), 0) || 1)
  const intensityOf = (v: number, c: number) => (normalize === 'column' ? v / colMax[c] : v / globalMax)
  const width = LEFT + cols.length * (CELL_W + GAP)
  const height = TOP + rows.length * (CELL_H + GAP)

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 'auto', display: 'block', maxWidth: width }} role="img" aria-label="Mapa de calor">
      {cols.map((col, c) => (
        <text key={c} x={LEFT + c * (CELL_W + GAP) + CELL_W / 2} y={TOP - 10} textAnchor="middle" style={{ fontSize: 11, fill: 'var(--fg-3)' }}>
          {col.length > 9 ? `${col.slice(0, 8)}…` : col}
        </text>
      ))}
      {rows.map((row, r) => (
        <g key={r}>
          <text x={LEFT - 10} y={TOP + r * (CELL_H + GAP) + CELL_H / 2} textAnchor="end" dominantBaseline="middle" style={{ fontSize: 11, fill: 'var(--fg-3)' }}>
            {row.length > 12 ? `${row.slice(0, 11)}…` : row}
          </text>
          {cols.map((_, c) => {
            const v = values[r]?.[c] ?? 0
            const intensity = intensityOf(v, c)
            const active = hover?.r === r && hover?.c === c
            return (
              <g key={c} onMouseEnter={() => setHover({ r, c })} onMouseLeave={() => setHover(null)}>
                <rect
                  x={LEFT + c * (CELL_W + GAP)}
                  y={TOP + r * (CELL_H + GAP)}
                  width={CELL_W}
                  height={CELL_H}
                  rx={5}
                  fill="var(--data-1)"
                  fillOpacity={0.08 + intensity * 0.82}
                  style={{ stroke: active ? 'var(--accent)' : 'transparent', transition: 'stroke var(--dur-fast) var(--ease)' }}
                  strokeWidth={1.5}
                />
                <text
                  x={LEFT + c * (CELL_W + GAP) + CELL_W / 2}
                  y={TOP + r * (CELL_H + GAP) + CELL_H / 2}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, fill: intensity > 0.55 ? 'var(--accent-ink)' : 'var(--fg-2)' }}
                >
                  {formatCompact(v)}
                </text>
              </g>
            )
          })}
        </g>
      ))}
    </svg>
  )
}
