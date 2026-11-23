import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'

export type PillTone = 'default' | 'ok' | 'warn' | 'danger' | 'accent'

export interface PillProps {
  tone?: PillTone
  /** Leading status dot (`.dot`). */
  dot?: boolean
  className?: string
  children: ReactNode
}

const TONE_CLASS: Record<PillTone, string | undefined> = {
  default: undefined,
  ok: 'pill-ok',
  warn: 'pill-warn',
  danger: undefined, // no dedicated class in alibi.css — colored inline
  accent: 'pill-accent',
}

/** Status pill (`.pill`). Danger tone uses inline color since alibi.css has no `.pill-danger`. */
export function Pill({ tone = 'default', dot = false, className, children }: PillProps) {
  return (
    <span
      className={cx('pill', TONE_CLASS[tone], className)}
      style={tone === 'danger' ? { color: 'var(--danger)' } : undefined}
    >
      {dot && <span className="dot" />}
      {children}
    </span>
  )
}
