import { useState } from 'react'
import {
  CartesianAxes,
  ChartTooltip,
  formatNum,
  innerH,
  innerW,
  niceMax,
  PLOT,
  type CategoricalDatum,
} from './chartCore'

export interface HistogramProps {
  /** Bins — label is the range, value is the frequency. */
  bins: CategoricalDatum[]
  color?: string
  valueFormat?: (n: number) => string
}

/** Frequency histogram — adjacent (touching) bars, single color. */
export function Histogram({ bins, color = 'var(--data-3)', valueFormat = formatNum }: HistogramProps) {
  const [hover, setHover] = useState<number | null>(null)
  const max = niceMax(Math.max(...bins.map((b) => b.value), 0)) || 1
  const band = innerW / bins.length
  const barW = band - 2

  return (
    <svg viewBox={`0 0 ${PLOT.w} ${PLOT.h}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label="Histograma">
      <CartesianAxes max={max} xLabels={bins.map((b) => b.label)} />
      {bins.map((b, i) => {
        const h = (b.value / max) * innerH
        const x = PLOT.m.left + band * i + 1
        const y = PLOT.m.top + innerH - h
        return (
          <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <rect
              x={x}
              y={y}
              width={barW}
              height={Math.max(h, 1)}
              rx={2}
              fill={color}
              fillOpacity={hover === i ? 0.95 : 0.7}
              style={{ transition: 'fill-opacity var(--dur) var(--ease)' }}
            />
          </g>
        )
      })}
      {hover !== null && (
        <ChartTooltip
          x={PLOT.m.left + band * (hover + 0.5)}
          y={PLOT.m.top + innerH - (bins[hover].value / max) * innerH}
          title={bins[hover].label}
          value={valueFormat(bins[hover].value)}
        />
      )}
    </svg>
  )
}
