/* ============================================================
   Display formatting.

   Both helpers exist because the alternative was inferring the same thing from
   a prebuilt string: the library used to read a relative date out of the last
   ` · ` segment of `Dataset.meta`, which broke the moment `meta` ended with
   something else ("Updated Sheet “Costs”"). Derive from the typed field,
   never from the sentence built for display.
   ============================================================ */

const DAY_MS = 86_400_000

/** "hoy" / "ayer" / "hace 3 días" / "hace 2 meses", from an ISO date. */
export function relativeDate(iso: string | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return '—'

  // Compared at day granularity, so something saved last night reads as
  // "ayer" rather than "hace 14 horas".
  const startOfDay = (d: Date) => Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())
  const days = Math.round((startOfDay(new Date()) - startOfDay(then)) / DAY_MS)

  if (days <= 0) return 'hoy'
  if (days === 1) return 'ayer'
  if (days < 30) return `hace ${days} días`
  const months = Math.round(days / 30)
  if (months < 12) return `hace ${months} ${months === 1 ? 'mes' : 'meses'}`
  const years = Math.round(months / 12)
  return `hace ${years} ${years === 1 ? 'año' : 'años'}`
}

/**
 * en-US number: `1,234.56`, with a fixed number of decimals.
 *
 * The locale is not cosmetic, and the reason is worth keeping even though the
 * code that proved it is gone. `deriveChartData.ts` used to parse cells back
 * out of rendered tables assuming comma-as-thousands / dot-as-decimal; while
 * this formatted es-AR (`1.234,56`) the round-trip read `1.23456`, so every
 * chart derived from a results table plotted wrong values. Charts no longer
 * round-trip through formatted text — Vega gets the raw rows — but anything
 * that parses this output back into a number inherits the same trap.
 */
export function formatNumber(value: number, decimals: number): string {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

/**
 * Decimals for a whole column: none if every value is a whole number, two
 * otherwise.
 *
 * Two decisions in one. **Per column, not per value**, because a money column
 * rendered `1,234.56 / 980 / 2,500.10` reads as three different kinds of
 * number — one value with cents means every value shows cents.
 *
 * And **two rather than "as many as needed"**, because a float cannot tell us
 * how the value was written: `75.50` in the file arrives as `75.5`, and
 * measuring its decimals yields one, which is wrong for the money this mostly
 * is. Two also matches what the backend already does when it formats sample
 * values, so the "Examples" column and the preview agree.
 */
export function columnDecimals(values: unknown[]): number {
  const hasFraction = values.some((v) => typeof v === 'number' && !Number.isInteger(v))
  return hasFraction ? 2 : 0
}

/**
 * A cell as the user should read it. Only real numbers are reformatted —
 * anything already a string was formatted by whoever produced it, and
 * re-parsing it would be guessing.
 */
export function formatCell(value: unknown, decimals = 0): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return formatNumber(value, decimals)
  return String(value)
}

/**
 * Pesos as a price label: `$ 39.900`, `$ 40`, `$ 1.234,50`.
 *
 * es-AR on purpose, unlike `formatNumber`: this is a price shown to an
 * Argentine customer, display-only, and never parsed back into a number (the
 * trap `formatNumber`'s comment describes does not apply). Decimals only when
 * the amount has cents.
 */
export function formatArs(value: number): string {
  const cents = !Number.isInteger(value)
  return `$ ${value.toLocaleString('es-AR', {
    minimumFractionDigits: cents ? 2 : 0,
    maximumFractionDigits: cents ? 2 : 0,
  })}`
}

/**
 * A USD reference value as a label: `US$ 20`, `US$ 16`, `US$ 0,08`. Same
 * es-AR rules as `formatArs`; cents always two digits when there are any.
 */
export function formatUsd(value: number): string {
  const cents = !Number.isInteger(value)
  return `US$ ${value.toLocaleString('es-AR', {
    minimumFractionDigits: cents ? 2 : 0,
    maximumFractionDigits: cents ? 2 : 0,
  })}`
}

/** An ISO date (`2026-10-01`) as `1/10/2026`, without a timezone shift. */
export function formatIsoDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return y && m && d ? `${d}/${m}/${y}` : iso
}
