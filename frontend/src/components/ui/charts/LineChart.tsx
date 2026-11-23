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

export interface LineChartProps {
  data: CategoricalDatum[]
  color?: string
  valueFormat?: (n: number) => string
}

/** Time-series / trend line with area gradient, glow and hover crosshair. */
export function LineChart({ data, color = 'var(--data-1)', valueFormat = formatNum }: LineChartProps) {
  const [hover, setHover] = useState<number | null>(null)
  const max = niceMax(Math.max(...data.map((d) => d.value), 0)) || 1
  const stepX = innerW / Math.max(data.length - 1, 1)
  const px = (i: number) => PLOT.m.left + stepX * i
  const py = (v: number) => PLOT.m.top + innerH - (v / max) * innerH
  const baseline = PLOT.m.top + innerH

  const line = data.map((d, i) => `${i ? 'L' : 'M'}${px(i)},${py(d.value)}`).join(' ')
  const area = `${line} L${px(data.length - 1)},${baseline} L${px(0)},${baseline} Z`
  const gradId = 'line-area'

  return (
    <svg viewBox={`0 0 ${PLOT.w} ${PLOT.h}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label="Serie temporal">
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.28} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <CartesianAxes max={max} xLabels={data.map((d) => d.label)} />
      <path d={area} fill={`url(#${gradId})`} />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth={2.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        style={{ filter: 'drop-shadow(0 3px 8px var(--accent-glow))' }}
      />
      {data.map((d, i) => (
        <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
          <rect x={px(i) - stepX / 2} y={PLOT.m.top} width={stepX} height={innerH} fill="transparent" />
          <circle cx={px(i)} cy={py(d.value)} r={hover === i ? 5 : 3} fill="var(--canvas-0)" stroke={color} strokeWidth={2} />
        </g>
      ))}
      {hover !== null && (
        <>
          <line x1={px(hover)} x2={px(hover)} y1={PLOT.m.top} y2={baseline} style={{ stroke: 'var(--glass-border-bright)' }} strokeDasharray="3 3" />
          <ChartTooltip x={px(hover)} y={py(data[hover].value)} title={data[hover].label} value={valueFormat(data[hover].value)} />
        </>
      )}
    </svg>
  )
}
