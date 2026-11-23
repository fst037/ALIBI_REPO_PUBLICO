import { useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { cx } from '../../lib/cx'
import styles from './CodeInput.module.css'

export interface CodeInputProps {
  /** Number of digit boxes. */
  length?: number
  /** Controlled value — one entry per box. */
  value: string[]
  onChange: (next: string[]) => void
  'aria-label'?: string
}

/**
 * Fixed-length numeric code entry (email verification). Advances focus
 * on input and steps back on backspace — via refs, not DOM queries.
 */
export function CodeInput({ length = 6, value, onChange, 'aria-label': ariaLabel }: CodeInputProps) {
  const refs = useRef<(HTMLInputElement | null)[]>([])

  const setDigit = (i: number, raw: string) => {
    const digit = raw.replace(/\D/g, '').slice(0, 1)
    const next = [...value]
    next[i] = digit
    onChange(next)
    if (digit && i < length - 1) refs.current[i + 1]?.focus()
  }

  const onKeyDown = (i: number, e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace' && !value[i] && i > 0) refs.current[i - 1]?.focus()
  }

  return (
    <div className={styles.row} role="group" aria-label={ariaLabel}>
      {Array.from({ length }, (_, i) => (
        <input
          key={i}
          ref={(el) => {
            refs.current[i] = el
          }}
          className={cx(styles.box, value[i] && styles.filled)}
          maxLength={1}
          inputMode="numeric"
          autoComplete="one-time-code"
          value={value[i] ?? ''}
          onChange={(e) => setDigit(i, e.target.value)}
          onKeyDown={(e) => onKeyDown(i, e)}
          aria-label={`Dígito ${i + 1}`}
        />
      ))}
    </div>
  )
}
