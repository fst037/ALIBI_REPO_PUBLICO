import { DATA_COLORS } from '../../components/ui'
import type { ChartType } from '../../types'

export interface ChartPreviewProps {
  chartType: Exclude<ChartType, 'table'>
  /**
   * Whether the spec encodes a categorical `color` channel. Decorative only —
   * this draws a stand-in shape from `chartType` plus this one bit, never the
   * chart's real values, so a saved chart is recognisable in a list without
   * fetching (and re-running) its query just to render a thumbnail.
   */
  multiColor?: boolean
}

const W = 64
const H = 38
const ACCENT = DATA_COLORS[0]

/** `n` colours: one accent repeated, or the palette cycled. */
function palette(n: number, multi: boolean): string[] {
  return Array.from({ length: n }, (_, i) => (multi ? DATA_COLORS[i % DATA_COLORS.length] : ACCENT))
}

function Bars({ heights, colors, gap = 5 }: { heights: number[]; colors: string[]; gap?: number }) {
  const n = heights.length
  const barW = (W - gap * (n + 1)) / n
  return (
    <>
      {heights.map((h, i) => {
        const x = gap + i * (barW + gap)
        const y = H - h - 3
        return <rect key={i} x={x} y={y} width={barW} height={h} rx={1.5} fill={colors[i % colors.length]} />
      })}
    </>
  )
}

const BAR_HEIGHTS = [14, 26, 9, 20]
const HISTOGRAM_HEIGHTS = [8, 16, 24, 30, 19, 12, 6]

function LinePreview({ multi }: { multi: boolean }) {
  const series = multi
    ? [
        { points: '2,26 16,18 30,22 44,9 62,14', color: DATA_COLORS[0] },
        { points: '2,32 16,30 30,24 44,27 62,20', color: DATA_COLORS[1] },
      ]
    : [{ points: '2,30 16,14 30,20 44,7 62,17', color: ACCENT }]
  return (
    <>
      {series.map((s, i) => (
        <polyline
          key={i}
          points={s.points}
          fill="none"
          stroke={s.color}
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </>
  )
}

const SCATTER_POINTS: [number, number][] = [
  [8, 28], [18, 14], [26, 24], [34, 8], [44, 20], [52, 12], [58, 30], [22, 33],
]

function ScatterPreview({ multi }: { multi: boolean }) {
  const colors = palette(3, multi)
  return (
    <>
      {SCATTER_POINTS.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={2.6} fill={colors[i % colors.length]} opacity={0.9} />
      ))}
    </>
  )
}

const PIE_SLICES = [40, 25, 20, 15]

/** `d`s for a small pie starting at 12 o'clock, going clockwise. Decorative — the
 * proportions are fixed, never the chart's real values (see `ChartPreviewProps`). */
function PiePreview() {
  const cx = W / 2
  const cy = H / 2 + 1
  const r = H / 2 - 3
  let angle = -90
  const colors = palette(PIE_SLICES.length, true)
  return (
    <>
      {PIE_SLICES.map((pct, i) => {
        const sweep = (pct / 100) * 360
        const start = (angle * Math.PI) / 180
        const end = ((angle + sweep) * Math.PI) / 180
        const x1 = cx + r * Math.cos(start)
        const y1 = cy + r * Math.sin(start)
        const x2 = cx + r * Math.cos(end)
        const y2 = cy + r * Math.sin(end)
        const large = sweep > 180 ? 1 : 0
        angle += sweep
        return (
          <path
            key={i}
            d={`M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large} 1 ${x2},${y2} Z`}
            fill={colors[i]}
          />
        )
      })}
    </>
  )
}

/** A 4×3 grid of cells sampled from the same ramp `vegaTheme.ts` gives a
 * heatmap — magnitude, not category, so this ignores `multiColor`. */
function HeatmapPreview() {
  const ramp = ['#141417', '#0f6e4d', '#1FCB8C', '#27F5A9']
  const cols = 4
  const rows = 3
  const cw = W / cols
  const ch = H / rows
  // A fixed, arbitrary intensity pattern — just enough variation to read as
  // "a heatmap" rather than a flat swatch.
  const intensity = [1, 3, 0, 2, 2, 0, 3, 1, 0, 2, 1, 3]
  return (
    <>
      {intensity.map((level, i) => {
        const col = i % cols
        const row = Math.floor(i / cols)
        return (
          <rect
            key={i}
            x={col * cw + 1}
            y={row * ch + 1}
            width={cw - 2}
            height={ch - 2}
            rx={1}
            fill={ramp[level]}
          />
        )
      })}
    </>
  )
}

/**
 * A stand-in glyph for a saved chart: shape from `chartType`, colour count
 * from whether the spec had a `color` channel. Not the chart's data — the
 * point is telling a bar chart from a line from a heatmap in a list, and
 * whether it reads as one series or several, without a network round trip
 * per row.
 */
export function ChartPreview({ chartType, multiColor = false }: ChartPreviewProps) {
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="100%" aria-hidden focusable="false">
      {chartType === 'bar' && <Bars heights={BAR_HEIGHTS} colors={palette(BAR_HEIGHTS.length, multiColor)} />}
      {chartType === 'histogram' && (
        <Bars heights={HISTOGRAM_HEIGHTS} colors={palette(HISTOGRAM_HEIGHTS.length, multiColor)} gap={2} />
      )}
      {chartType === 'line' && <LinePreview multi={multiColor} />}
      {chartType === 'scatter' && <ScatterPreview multi={multiColor} />}
      {chartType === 'pie' && <PiePreview />}
      {chartType === 'heatmap' && <HeatmapPreview />}
    </svg>
  )
}

/** Whether a Vega-Lite spec encodes a categorical `color` channel. */
export function specHasColorChannel(spec: Record<string, unknown> | undefined): boolean {
  const encoding = spec?.encoding
  return !!encoding && typeof encoding === 'object' && 'color' in (encoding as Record<string, unknown>)
}
