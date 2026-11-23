import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RunMode } from '../../lib/api'
import { Composer } from './Composer'

// Quick/Deep and the plan rule: Enter sends at the conversation's depth,
// Ctrl+Enter / the second button send one question at the other depth, and on
// Free (or past the quota) Profunda is locked and there is no override.

afterEach(cleanup)

function setup(deepLock: 'plan' | 'quota' | null = null, initial = '') {
  const onSend = vi.fn<(override?: RunMode) => void>()
  const onModeChange = vi.fn<(mode: RunMode) => void>()
  function Harness() {
    const [value, setValue] = useState(initial)
    return (
      <Composer value={value} onChange={setValue} onSend={onSend} mode="quick" onModeChange={onModeChange} deepLock={deepLock} />
    )
  }
  render(<MemoryRouter><Harness /></MemoryRouter>)
  return { onSend, onModeChange, box: screen.getByPlaceholderText('Preguntá sobre tus datos…') }
}

describe('Composer', () => {
  it('does not send an empty or blank question', () => {
    const { onSend, box } = setup(null, '   ')
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: 'Enviar' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('sends on Enter at the default depth, and Shift+Enter does not send', () => {
    const { onSend, box } = setup()
    fireEvent.change(box, { target: { value: '¿Qué sede vendió más?' } })
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true })
    expect(onSend).not.toHaveBeenCalled()
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith(undefined)
  })

  it('sends one question in Profunda with Ctrl+Enter or the second button', () => {
    const { onSend, box } = setup()
    fireEvent.change(box, { target: { value: '¿Por qué cayeron las ventas?' } })
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true })
    expect(onSend).toHaveBeenLastCalledWith('deep')
    fireEvent.click(screen.getByRole('button', { name: 'Enviar solo esta pregunta en modo Profunda' }))
    expect(onSend).toHaveBeenLastCalledWith('deep')
  })

  it('switches the default depth from the segmented control', () => {
    const { onModeChange } = setup()
    fireEvent.click(screen.getByRole('tab', { name: 'Profunda' }))
    expect(onModeChange).toHaveBeenCalledWith('deep')
  })

  it('on Free, locks Profunda, offers the plan, and Ctrl+Enter stays Quick', () => {
    const { onSend, onModeChange, box } = setup('plan')
    const deep = screen.getByRole('tab', { name: 'Profunda · Pro' }) as HTMLButtonElement
    expect(deep.disabled).toBe(true)
    fireEvent.click(deep)
    expect(onModeChange).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /modo Profunda/ })).toBeNull()
    expect(screen.getByRole('link', { name: /Profunda con Pro/ }).getAttribute('href')).toBe('/cuenta?tab=billing')

    fireEvent.change(box, { target: { value: 'hola' } })
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true })
    expect(onSend).toHaveBeenCalledWith(undefined)
  })

  it('past the quota, says extra queries are answered Quick', () => {
    setup('quota')
    expect((screen.getByRole('tab', { name: 'Profunda' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/Consultas adicionales: modo Rápido/)).toBeTruthy()
  })
})
