import type { CSSProperties } from 'react'

/* ============================================================
   Shared chart plumbing — logical SVG canvas, scales, axes.
   All charts render into a fixed viewBox and scale to 100% width,
   so tooltips/labels live in the same coordinate space (no px mapping).
   Colors come from the --data-* palette in tokens.css.
   ============================================================ */

export interface CategoricalDatum {
  label: string
  value: number
}

export interface ScatterPoint {
  x: number
  y: number
  label?: string
}

export const PLOT = {
  w: 560,
  h: 300,
  m: { top: 18, right: 18, bottom: 42, left: 54 },
} as const

export const innerW = PLOT.w - PLOT.m.left - PLOT.m.right
export const innerH = PLOT.h - PLOT.m.top - PLOT.m.bottom

const DATA_VARS = ['--data-1', '--data-2', '--data-3', '--data-4', '--data-5', '--data-6'] as const

/** Series color from the mint-anchored data palette, cycling. */
export function dataColor(index: number): string {
  return `var(${DATA_VARS[index % DATA_VARS.length]})`
}

/** Round a value up to a "nice" axis maximum (1/2/5 × 10ⁿ). */
export function niceMax(value: number): number {
  if (value <= 0) return 1
  const exp = Math.floor(Math.log10(value))
  const base = 10 ** exp
  const frac = value / base
  const nice = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10
  return nice * base
}

/** Evenly spaced ticks 0..max (max should already be nice). */
export function linTicks(max: number, count = 4): number[] {
  return Array.from({ length: count + 1 }, (_, i) => (max / count) * i)
}

export function formatNum(n: number): string {
  return n.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

/** Compact axis/label formatting (1.2k, 3,4M). */
export function formatCompact(n: number): string {
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${(n / 1_000_000).toLocaleString('en-US', { maximumFractionDigits: 1 })}M`
  if (abs >= 1_000) return `${(n / 1_000).toLocaleString('en-US', { maximumFractionDigits: 1 })}k`
  return formatNum(n)
}

export const tickStyle: CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: 10, fill: 'var(--fg-3)' }
export const xLabStyle: CSSProperties = { fontFamily: 'var(--font-sans)', fontSize: 11, fill: 'var(--fg-3)' }

/** Y gridlines + mono tick labels and centered x-band labels. */
export function CartesianAxes({ max, xLabels }: { max: number; xLabels: string[] }) {
  const band = innerW / xLabels.length
  return (
    <g>
      {linTicks(max).map((t) => {
        const y = PLOT.m.top + innerH - (t / max) * innerH
        return (
          <g key={t}>
            <line
              x1={PLOT.m.left}
              x2={PLOT.m.left + innerW}
              y1={y}
              y2={y}
              style={{ stroke: 'var(--glass-border)' }}
              strokeWidth={1}
            />
            <text x={PLOT.m.left - 8} y={y} textAnchor="end" dominantBaseline="middle" style={tickStyle}>
              {formatCompact(t)}
            </text>
          </g>
        )
      })}
      {xLabels.map((label, i) => (
        <text key={i} x={PLOT.m.left + band * (i + 0.5)} y={PLOT.m.top + innerH + 20} textAnchor="middle" style={xLabStyle}>
          {label.length > 10 ? `${label.slice(0, 9)}…` : label}
        </text>
      ))}
    </g>
  )
}

/** SVG-native tooltip anchored above a point, clamped to the canvas. */
export function ChartTooltip({ x, y, title, value }: { x: number; y: number; title: string; value: string }) {
  const w = Math.max(title.length, value.length) * 6.6 + 22
  const h = 40
  const tx = Math.min(Math.max(x - w / 2, 2), PLOT.w - w - 2)
  const ty = Math.max(y - h - 12, 2)
  return (
    <g pointerEvents="none">
      <rect
        x={tx}
        y={ty}
        width={w}
        height={h}
        rx={8}
        style={{ fill: 'var(--canvas-0)', stroke: 'var(--glass-border-bright)' }}
        strokeWidth={1}
      />
      <text x={tx + 11} y={ty + 16} style={{ fontSize: 10, fill: 'var(--fg-3)' }}>
        {title.length > 22 ? `${title.slice(0, 21)}…` : title}
      </text>
      <text x={tx + 11} y={ty + 31} style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, fill: 'var(--fg-1)' }}>
        {value}
      </text>
    </g>
  )
}
