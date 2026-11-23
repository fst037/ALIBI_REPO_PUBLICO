import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { columnDecimals, formatArs, formatCell, formatIsoDate, formatNumber, formatUsd, relativeDate } from './format'

describe('relativeDate', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-22T15:00:00Z'))
  })
  afterEach(() => vi.useRealTimers())

  it.each([
    ['2026-11-22T01:00:00Z', 'hoy'],
    ['2026-11-21T23:59:00Z', 'ayer'],
    ['2026-11-19T12:00:00Z', 'hace 3 días'],
    ['2026-10-22T12:00:00Z', 'hace 1 mes'],
    ['2026-08-22T12:00:00Z', 'hace 3 meses'],
    ['2024-11-22T12:00:00Z', 'hace 2 años'],
  ])('%s → %s', (iso, expected) => {
    expect(relativeDate(iso)).toBe(expected)
  })

  it('reads a future date as today and a missing or invalid one as a dash', () => {
    expect(relativeDate('2026-12-01T00:00:00Z')).toBe('hoy')
    expect(relativeDate(undefined)).toBe('—')
    expect(relativeDate('no es una fecha')).toBe('—')
  })
})

describe('numbers in result tables', () => {
  // en-US on purpose (see format.ts): anything parsing this back must not read 1.234,56 as 1.23456.
  it('formats with en-US separators and fixed decimals', () => {
    expect(formatNumber(1234.5, 2)).toBe('1,234.50')
    expect(formatNumber(1234567, 0)).toBe('1,234,567')
  })

  it('gives a whole column two decimals as soon as one value has cents', () => {
    expect(columnDecimals([1, 2, 3])).toBe(0)
    expect(columnDecimals([1, 2.5, 'x'])).toBe(2)
    expect(columnDecimals([])).toBe(0)
  })

  it('reformats only real numbers, and shows a dash for a missing cell', () => {
    expect(formatCell(980, 2)).toBe('980.00')
    expect(formatCell('1.234,56')).toBe('1.234,56')
    expect(formatCell(null)).toBe('—')
    expect(formatCell(undefined)).toBe('—')
  })
})

describe('prices', () => {
  it('shows pesos and dollars the Argentine way, cents only when there are any', () => {
    expect(formatArs(39900)).toBe('$ 39.900')
    expect(formatArs(1234.5)).toBe('$ 1.234,50')
    expect(formatUsd(20)).toBe('US$ 20')
    expect(formatUsd(0.08)).toBe('US$ 0,08')
  })
})

describe('formatIsoDate', () => {
  it('reads the date part without a timezone shift', () => {
    expect(formatIsoDate('2026-10-01')).toBe('1/10/2026')
    expect(formatIsoDate('2026-10-01T23:30:00-03:00')).toBe('1/10/2026')
    expect(formatIsoDate('mal')).toBe('mal')
  })
})
