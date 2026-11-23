import { useState } from 'react'
import {
  ChartTooltip,
  formatCompact,
  formatNum,
  innerH,
  innerW,
  linTicks,
  niceMax,
  PLOT,
  tickStyle,
  xLabStyle,
  type ScatterPoint,
} from './chartCore'

export interface ScatterChartProps {
  points: ScatterPoint[]
  color?: string
}

/** Scatter plot with numeric x/y axes and per-point hover tooltip. */
export function ScatterChart({ points, color = 'var(--data-2)' }: ScatterChartProps) {
  const [hover, setHover] = useState<number | null>(null)
  const maxX = niceMax(Math.max(...points.map((p) => p.x), 0)) || 1
  const maxY = niceMax(Math.max(...points.map((p) => p.y), 0)) || 1
  const px = (x: number) => PLOT.m.left + (x / maxX) * innerW
  const py = (y: number) => PLOT.m.top + innerH - (y / maxY) * innerH

  return (
    <svg viewBox={`0 0 ${PLOT.w} ${PLOT.h}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label="Gráfico de dispersión">
      {linTicks(maxY).map((t) => {
        const y = py(t)
        return (
          <g key={`y${t}`}>
            <line x1={PLOT.m.left} x2={PLOT.m.left + innerW} y1={y} y2={y} style={{ stroke: 'var(--glass-border)' }} strokeWidth={1} />
            <text x={PLOT.m.left - 8} y={y} textAnchor="end" dominantBaseline="middle" style={tickStyle}>
              {formatCompact(t)}
            </text>
          </g>
        )
      })}
      {linTicks(maxX).map((t) => (
        <text key={`x${t}`} x={px(t)} y={PLOT.m.top + innerH + 20} textAnchor="middle" style={xLabStyle}>
          {formatCompact(t)}
        </text>
      ))}
      {points.map((p, i) => (
        <circle
          key={i}
          cx={px(p.x)}
          cy={py(p.y)}
          r={hover === i ? 7 : 4.5}
          fill={color}
          fillOpacity={0.7}
          stroke={color}
          strokeWidth={hover === i ? 1.5 : 0}
          style={{ transition: 'r var(--dur-fast) var(--ease)' }}
          onMouseEnter={() => setHover(i)}
          onMouseLeave={() => setHover(null)}
        />
      ))}
      {hover !== null && (
        <ChartTooltip
          x={px(points[hover].x)}
          y={py(points[hover].y)}
          title={points[hover].label ?? 'Punto'}
          value={`${formatNum(points[hover].x)} · ${formatNum(points[hover].y)}`}
        />
      )}
    </svg>
  )
}
