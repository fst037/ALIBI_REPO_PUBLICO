import type { ButtonHTMLAttributes } from 'react'
import { cx } from '../../lib/cx'

export type ButtonVariant = 'primary' | 'glass' | 'ghost'
export type ButtonSize = 'md' | 'sm'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Square icon-only button (`.btn-icon`). */
  iconOnly?: boolean
}

/** Base button — maps to `.btn` + variant classes from alibi.css. */
export function Button({
  variant = 'primary',
  size = 'md',
  iconOnly = false,
  className,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        'btn',
        `btn-${variant}`,
        size === 'sm' && 'btn-sm',
        iconOnly && 'btn-icon',
        className,
      )}
      {...rest}
    />
  )
}
