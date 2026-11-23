import type { ReactNode } from 'react'
import { CheckCircle, Info, Warning, XCircle } from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { cx } from '../../lib/cx'
import { Icon } from './Icon'

export type ToastTone = 'ok' | 'warn' | 'err' | 'info'

export interface ToastProps {
  tone?: ToastTone
  title: ReactNode
  children?: ReactNode
  className?: string
}

const TONE_ICON: Record<ToastTone, PhosphorIcon> = {
  ok: CheckCircle,
  warn: Warning,
  err: XCircle,
  info: Info,
}

/** In-app toast (`.toast`) — receipts, warnings, errors. */
export function Toast({ tone = 'info', title, children, className }: ToastProps) {
  return (
    <div className={cx('toast', className)} role="status">
      <div className={cx('toast-ico', tone)}>
        <Icon icon={TONE_ICON[tone]} size={18} weight="fill" />
      </div>
      <div>
        <p className="toast-title">{title}</p>
        {children && <p className="toast-body">{children}</p>}
      </div>
    </div>
  )
}
