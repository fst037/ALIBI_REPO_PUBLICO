import type { ButtonHTMLAttributes } from 'react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { cx } from '../../lib/cx'
import { Icon } from './Icon'

export interface ChipProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: PhosphorIcon
}

/** Interactive chip (`.chip`) — e.g. recent-question shortcuts, filters. */
export function Chip({ icon, className, children, type = 'button', ...rest }: ChipProps) {
  return (
    <button type={type} className={cx('chip', className)} {...rest}>
      {icon && <Icon icon={icon} size="sm" />}
      {children}
    </button>
  )
}
