import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'

export interface EyebrowProps {
  className?: string
  children: ReactNode
}

/** Uppercase label above a section/heading (`.eyebrow`). */
export function Eyebrow({ className, children }: EyebrowProps) {
  return <div className={cx('eyebrow', className)}>{children}</div>
}
