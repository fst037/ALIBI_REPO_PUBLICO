import type { Config } from 'vega-lite'

/* ============================================================
   The ALIBI look, expressed as a Vega-Lite config.

   Module C emits a *minimal* spec — mark, encoding, titles — and is told
   explicitly not to set colours, sizes or a background (rule 8 of its system
   prompt). Everything that makes a chart look like this product is applied
   here instead, on every chart, so a spec from a model and a spec built by the
   chips are indistinguishable once drawn.

   **The values are literal hex, mirrored from `tokens.css`, and that is not a
   duplication bug.** Vega writes `fill`/`stroke` as SVG *attributes*, and
   `fill="var(--data-1)"` is not resolved the way the same string is inside a
   CSS declaration — the mark would render black. The hand-written SVG charts in
   this folder can use `var(--data-*)` because they set those values through
   React style props; Vega cannot. Anything below that a stylesheet *can* reach
   (the tooltip, the container) is left to `alibi.css`.

   Keep in sync with `src/styles/tokens.css` if the palette moves.
   ============================================================ */

const CANVAS_LINE = 'rgba(255, 255, 255, 0.10)' /* --glass-border */
const FG_2 = 'rgba(244, 246, 245, 0.64)'
const FG_3 = 'rgba(244, 246, 245, 0.42)'
const FG_1 = '#F4F6F5'

/** --data-1 … --data-6, mint-anchored. Series cycle through these in order. */
export const DATA_COLORS = ['#27F5A9', '#4FD2FF', '#9C8CFF', '#FFC56E', '#FF8FA3', '#5BE6C2']

const MONO = "'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace"
const SANS = "'Geist', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif"

export const alibiVegaConfig: Config = {
  // The answer card is already glass; a chart background would sit as an opaque
  // rectangle on top of it.
  background: 'transparent',
  font: SANS,
  padding: 4,

  view: { stroke: null },

  axis: {
    // Numbers in JetBrains Mono, everywhere they appear — the same rule the
    // results table and the SQL block follow.
    labelFont: MONO,
    labelFontSize: 10,
    labelColor: FG_3,
    labelPadding: 6,
    titleFont: SANS,
    titleFontSize: 11,
    titleFontWeight: 500,
    titleColor: FG_2,
    titlePadding: 10,
    gridColor: CANVAS_LINE,
    gridWidth: 1,
    domain: false,
    tickColor: CANVAS_LINE,
    tickSize: 4,
  },
  // Category labels are words, not quantities, so they get the sans face.
  axisX: { labelFont: SANS, labelFontSize: 11, labelAngle: 0, grid: false },
  axisY: { grid: true },
  // …except when the x axis carries a scale rather than names.
  axisXQuantitative: { labelFont: MONO, labelFontSize: 10, grid: true },
  axisXTemporal: { labelFont: MONO, labelFontSize: 10, grid: false },

  legend: {
    labelFont: SANS,
    labelFontSize: 11,
    labelColor: FG_2,
    titleFont: SANS,
    titleFontSize: 11,
    titleColor: FG_3,
    titleFontWeight: 500,
    symbolType: 'circle',
    symbolSize: 60,
    orient: 'top',
    direction: 'horizontal',
    offset: 4,
  },

  title: {
    font: SANS,
    fontSize: 13,
    fontWeight: 600,
    color: FG_1,
    anchor: 'start',
    offset: 12,
    subtitleFont: SANS,
    subtitleColor: FG_3,
  },

  range: {
    category: DATA_COLORS,
    // Sequential ramps (heatmaps) run from the canvas up to the mint accent, so
    // an empty cell reads as absent rather than as a dark colour of its own.
    heatmap: ['#141417', '#0f6e4d', '#1FCB8C', '#27F5A9'],
    ramp: ['#141417', '#1FCB8C', '#27F5A9'],
  },

  mark: { color: DATA_COLORS[0], tooltip: true },
  bar: { cornerRadiusEnd: 3, color: DATA_COLORS[0] },
  line: { strokeWidth: 2, color: DATA_COLORS[0], point: false },
  area: { line: { strokeWidth: 2 }, opacity: 0.28, color: DATA_COLORS[0] },
  point: { size: 60, filled: true, opacity: 0.85, color: DATA_COLORS[0] },
  circle: { size: 60, opacity: 0.85, color: DATA_COLORS[0] },
  rect: { color: DATA_COLORS[0] },
  arc: { innerRadius: 42, padAngle: 0.015 },
  text: { font: MONO, fontSize: 10, fill: FG_2 },
}

/* ---- The same look for paper: dark ink on white ----------------------------
   The PDF report is read on a white page and may be printed, so it cannot use
   the on-screen palette (light text, mint on near-black). Same structure,
   darker values: the mint that glows on black is washed out on white, so the
   series colours are deeper hues of the same family. */

const PRINT_INK = '#111114'
const PRINT_INK_2 = 'rgba(17, 17, 20, 0.72)'
const PRINT_INK_3 = 'rgba(17, 17, 20, 0.55)'
const PRINT_LINE = 'rgba(17, 17, 20, 0.14)'

export const PRINT_DATA_COLORS = ['#0E9F6E', '#0B8FBF', '#6E5BE0', '#D98A00', '#D9485F', '#2BA88A']

export const alibiVegaPrintConfig: Config = {
  ...alibiVegaConfig,
  background: '#FFFFFF',
  axis: {
    ...alibiVegaConfig.axis,
    labelColor: PRINT_INK_3,
    titleColor: PRINT_INK_2,
    gridColor: PRINT_LINE,
    tickColor: PRINT_LINE,
  },
  legend: { ...alibiVegaConfig.legend, labelColor: PRINT_INK_2, titleColor: PRINT_INK_3 },
  title: { ...alibiVegaConfig.title, color: PRINT_INK, subtitleColor: PRINT_INK_3 },
  range: {
    category: PRINT_DATA_COLORS,
    heatmap: ['#F1F5F3', '#7FD9B7', '#2BB583', '#0E9F6E'],
    ramp: ['#F1F5F3', '#2BB583', '#0E9F6E'],
  },
  mark: { color: PRINT_DATA_COLORS[0], tooltip: true },
  bar: { cornerRadiusEnd: 3, color: PRINT_DATA_COLORS[0] },
  line: { strokeWidth: 2, color: PRINT_DATA_COLORS[0], point: false },
  area: { line: { strokeWidth: 2 }, opacity: 0.28, color: PRINT_DATA_COLORS[0] },
  point: { size: 60, filled: true, opacity: 0.85, color: PRINT_DATA_COLORS[0] },
  circle: { size: 60, opacity: 0.85, color: PRINT_DATA_COLORS[0] },
  rect: { color: PRINT_DATA_COLORS[0] },
  text: { ...alibiVegaConfig.text, fill: PRINT_INK_2 },
}
