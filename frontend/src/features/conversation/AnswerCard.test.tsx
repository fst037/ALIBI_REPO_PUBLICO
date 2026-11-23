import type { ReactNode } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ChartSpec, QueryResult } from '../../types'
import { LegalContext } from '../legal/legalContext'
import { AnswerCard } from './AnswerCard'

// An answer reads sentence → chart → rows, the SQL and
// the rows sit behind a disclosure, the rows are open only when there is no
// chart, several findings each get their own block, and "Cómo llegué a esto"
// lists every step, failures included.

// Vega needs a real canvas; what is tested here is the layout around the chart.
vi.mock('./AnswerChart', () => ({
  AnswerChart: ({ chart }: { chart: ChartSpec }) => <div data-testid="chart">{chart.chartType}</div>,
}))
vi.mock('../export/ExportMenu', () => ({ ExportMenu: () => null }))

afterEach(cleanup)

const TABLE = {
  columns: [
    { key: 'sede', label: 'Sede', align: 'left' as const },
    { key: 'ventas', label: 'Ventas', align: 'num' as const },
  ],
  rows: [
    { sede: 'Palermo', ventas: 1520.5 },
    { sede: 'Belgrano', ventas: 980 },
  ],
}
const CHART: ChartSpec = { chartType: 'bar', spec: { mark: 'bar' }, columns: [] }

function answer(overrides: Partial<QueryResult> = {}): QueryResult {
  return {
    naturalResponse: 'Palermo vendió más que Belgrano.',
    sql: 'SELECT sede, SUM(total) AS ventas FROM ventas GROUP BY sede',
    table: TABLE,
    rowCount: 2,
    rowsLabel: '2 filas',
    chart: null,
    ...overrides,
  }
}

function renderCard(result: QueryResult) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <LegalContext.Provider value={{ openLegal: () => {} }}>{children}</LegalContext.Provider>
  )
  return render(
    <AnswerCard result={result} sourceLabel="Ventas 2024" question="¿Qué sede vendió más?" datasetId="f1" datasetName="Ventas 2024" />,
    { wrapper },
  )
}

const dataControl = () => screen.getByRole('button', { name: /Datos · 2 filas/ })

describe('AnswerCard', () => {
  it('shows the sentence, the chart, and keeps the rows and SQL closed when there is a chart', () => {
    renderCard(answer({ chart: CHART }))
    expect(screen.getByText('Palermo vendió más que Belgrano.')).toBeTruthy()
    expect(screen.getByTestId('chart').textContent).toBe('bar')
    expect(dataControl().getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByRole('button', { name: /SQL generado/ }).getAttribute('aria-expanded')).toBe('false')
  })

  it('opens the rows when there is no chart, since they are the only visual', () => {
    renderCard(answer({ chart: null }))
    expect(screen.queryByTestId('chart')).toBeNull()
    expect(dataControl().getAttribute('aria-expanded')).toBe('true')
    const table = screen.getByRole('table')
    expect(within(table).getByText('Palermo')).toBeTruthy()
    // One value with cents gives the whole column cents.
    expect(within(table).getByText('1,520.50')).toBeTruthy()
    expect(within(table).getByText('980.00')).toBeTruthy()
  })

  it('opens the rows on demand when they start closed', () => {
    renderCard(answer({ chart: CHART }))
    fireEvent.click(dataControl())
    expect(screen.getByRole('table')).toBeTruthy()
  })

  it('gives each finding its own sentence and evidence when there are several', () => {
    const second = { ...answer(), text: 'Belgrano cayó en agosto.', question: 'q2', sql: 'SELECT 2', chart: CHART }
    const first = { ...answer(), text: 'Palermo lidera.', question: 'q1', sql: 'SELECT 1', chart: null }
    renderCard(answer({ findings: [first, second] }))
    expect(screen.getByText('2 hallazgos')).toBeTruthy()
    expect(screen.getByText('Palermo lidera.')).toBeTruthy()
    expect(screen.getByText('Belgrano cayó en agosto.')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /SQL generado/ })).toHaveLength(2)
    expect(screen.getAllByTestId('chart')).toHaveLength(1)
  })

  it('lists every step in "Cómo llegué a esto", the failed one with its error', () => {
    renderCard(
      answer({
        trail: [
          { label: 'Cuántas filas hay', purpose: 'context', status: 'done', rowCount: 1, sql: 'SELECT COUNT(*) FROM ventas' },
          { label: 'Ventas por sede', purpose: 'insight', status: 'failed', error: 'Binder Error: columna "sedes" no existe' },
        ],
      }),
    )
    const control = screen.getByRole('button', { name: /Cómo llegué a esto · 2 pasos/ })
    expect(screen.queryByText('Cuántas filas hay')).toBeNull()
    fireEvent.click(control)
    expect(screen.getByText('Cuántas filas hay')).toBeTruthy()
    expect(screen.getByText('para entender los datos')).toBeTruthy()
    expect(screen.getByText(/Binder Error/)).toBeTruthy()
  })

  it('carries the AI notice next to every result', () => {
    renderCard(answer())
    expect(screen.getByRole('button', { name: 'Más información' })).toBeTruthy()
  })
})
