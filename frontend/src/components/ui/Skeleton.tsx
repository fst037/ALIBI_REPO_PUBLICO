import type { CSSProperties } from 'react'
import { cx } from '../../lib/cx'

export interface SkeletonProps {
  variant?: 'line' | 'block'
  width?: number | string
  height?: number | string
  className?: string
  style?: CSSProperties
}

/** Loading placeholder (`.skel`). Use instead of spinners for content. */
export function Skeleton({ variant = 'line', width, height, className, style }: SkeletonProps) {
  return (
    <div
      className={cx('skel', variant === 'line' ? 'skel-line' : 'skel-block', className)}
      style={{ width, height, ...style }}
    />
  )
}
