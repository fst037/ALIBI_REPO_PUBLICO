import type { ReactNode } from 'react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { cx } from '../../lib/cx'
import { Icon } from './Icon'

export interface EmptyStateProps {
  icon: PhosphorIcon
  title: ReactNode
  description?: ReactNode
  /** CTA(s) rendered below the copy. */
  action?: ReactNode
  className?: string
}

/** Full-panel empty state (`.empty`) — no data, no results, etc. */
export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cx('empty', className)}>
      <div className="empty-ico">
        <Icon icon={icon} size={28} />
      </div>
      <div>
        <div style={{ fontWeight: 600 }}>{title}</div>
        {description && (
          <p className="hint" style={{ margin: '5px 0 0', maxWidth: 260 }}>
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  )
}
