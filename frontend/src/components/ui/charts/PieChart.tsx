import { useState } from 'react'
import { dataColor, formatNum, type CategoricalDatum } from './chartCore'

export interface PieChartProps {
  data: CategoricalDatum[]
  valueFormat?: (n: number) => string
}

const SIZE = 240
const R = 100
const INNER = 58
const CX = SIZE / 2
const CY = SIZE / 2

function polar(cx: number, cy: number, r: number, angle: number): [number, number] {
  const a = (angle - 90) * (Math.PI / 180)
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)]
}

function donutSlice(start: number, end: number): string {
  const [sx, sy] = polar(CX, CY, R, end)
  const [ex, ey] = polar(CX, CY, R, start)
  const [isx, isy] = polar(CX, CY, INNER, end)
  const [iex, iey] = polar(CX, CY, INNER, start)
  const large = end - start > 180 ? 1 : 0
  return `M${sx},${sy} A${R},${R} 0 ${large} 0 ${ex},${ey} L${iex},${iey} A${INNER},${INNER} 0 ${large} 1 ${isx},${isy} Z`
}

/** Donut chart with legend. Best for part-to-whole with few categories. */
export function PieChart({ data, valueFormat = formatNum }: PieChartProps) {
  const [hover, setHover] = useState<number | null>(null)
  const total = data.reduce((sum, d) => sum + d.value, 0) || 1
  let acc = 0
  const slices = data.map((d, i) => {
    const start = (acc / total) * 360
    acc += d.value
    const end = (acc / total) * 360
    return { d, i, start, end, pct: (d.value / total) * 100 }
  })

  return (
    <div className="row" style={{ gap: 24, flexWrap: 'wrap', alignItems: 'center' }}>
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-label="Gráfico de torta">
        {slices.map(({ start, end, i }) => (
          <path
            key={i}
            d={donutSlice(start, end)}
            fill={dataColor(i)}
            fillOpacity={hover === null || hover === i ? 1 : 0.35}
            style={{ transition: 'fill-opacity var(--dur) var(--ease)' }}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
        <text x={CX} y={CY - 4} textAnchor="middle" style={{ fontFamily: 'var(--font-mono)', fontSize: 20, fill: 'var(--fg-1)' }}>
          {hover === null ? formatNum(total) : `${slices[hover].pct.toFixed(1)}%`}
        </text>
        <text x={CX} y={CY + 14} textAnchor="middle" style={{ fontSize: 10, fill: 'var(--fg-3)' }}>
          {hover === null ? 'total' : slices[hover].d.label}
        </text>
      </svg>
      <ul className="legend" style={{ flexDirection: 'column', gap: 9 }}>
        {slices.map(({ d, i, pct }) => (
          <li
            key={i}
            className="legend-item"
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            style={{ opacity: hover === null || hover === i ? 1 : 0.5, cursor: 'default' }}
          >
            <span className="legend-dot" style={{ background: dataColor(i) }} />
            <span className="grow">{d.label}</span>
            <span className="mono" style={{ color: 'var(--fg-2)', fontSize: 11.5 }}>
              {valueFormat(d.value)} · {pct.toFixed(0)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
