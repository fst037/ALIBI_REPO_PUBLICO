import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SqlBlock } from './SqlBlock'

// The SQL that was executed is behind a disclosure
// control, hidden until the user asks for it, and shown intact when they do.

afterEach(cleanup)

const SQL = 'SELECT sede, SUM(total) AS ventas FROM ventas GROUP BY sede ORDER BY ventas DESC'

describe('SqlBlock', () => {
  it('keeps the executed SQL hidden until the control is opened', () => {
    render(<SqlBlock sql={SQL} />)
    const control = screen.getByRole('button', { name: /SQL generado/ })
    expect(control.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText('ventas', { exact: false })).toBeNull()

    fireEvent.click(control)
    expect(control.getAttribute('aria-expanded')).toBe('true')
    expect(document.querySelector('.sql-body')?.textContent).toMatch(/SELECT\s+sede,\s+SUM\(total\)/)
  })

  it('hides it again on a second click', () => {
    render(<SqlBlock sql={SQL} />)
    const control = screen.getByRole('button', { name: /SQL generado/ })
    fireEvent.click(control)
    fireEvent.click(control)
    expect(document.querySelector('.sql-body')).toBeNull()
  })
})
