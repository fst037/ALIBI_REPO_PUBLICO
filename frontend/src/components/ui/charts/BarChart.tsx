import { useState } from 'react'
import {
  CartesianAxes,
  ChartTooltip,
  formatCompact,
  formatNum,
  innerH,
  innerW,
  niceMax,
  PLOT,
  tickStyle,
  type CategoricalDatum,
} from './chartCore'

export interface BarChartProps {
  data: CategoricalDatum[]
  color?: string
  valueFormat?: (n: number) => string
}

/** Vertical bar chart with real axis, value labels and hover tooltip. */
export function BarChart({ data, color = 'var(--data-1)', valueFormat = formatNum }: BarChartProps) {
  const [hover, setHover] = useState<number | null>(null)
  const max = niceMax(Math.max(...data.map((d) => d.value), 0)) || 1
  const band = innerW / data.length
  const barW = Math.min(band * 0.6, 54)
  const gradId = 'bar-grad'

  return (
    <svg viewBox={`0 0 ${PLOT.w} ${PLOT.h}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label="Gráfico de barras">
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.95} />
          <stop offset="100%" stopColor={color} stopOpacity={0.5} />
        </linearGradient>
      </defs>
      <CartesianAxes max={max} xLabels={data.map((d) => d.label)} />
      {data.map((d, i) => {
        const h = (d.value / max) * innerH
        const x = PLOT.m.left + band * i + (band - barW) / 2
        const y = PLOT.m.top + innerH - h
        const active = hover === i
        return (
          <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <rect x={PLOT.m.left + band * i} y={PLOT.m.top} width={band} height={innerH} fill="transparent" />
            <rect
              x={x}
              y={y}
              width={barW}
              height={Math.max(h, 1)}
              rx={5}
              fill={`url(#${gradId})`}
              style={{
                filter: active ? 'drop-shadow(0 0 10px var(--accent-glow))' : undefined,
                transition: 'filter var(--dur) var(--ease)',
              }}
            />
            {!active && (
              <text x={x + barW / 2} y={y - 6} textAnchor="middle" style={{ ...tickStyle, fill: 'var(--fg-2)' }}>
                {formatCompact(d.value)}
              </text>
            )}
          </g>
        )
      })}
      {hover !== null && (
        <ChartTooltip
          x={PLOT.m.left + band * hover + band / 2}
          y={PLOT.m.top + innerH - (data[hover].value / max) * innerH}
          title={data[hover].label}
          value={valueFormat(data[hover].value)}
        />
      )}
    </svg>
  )
}
