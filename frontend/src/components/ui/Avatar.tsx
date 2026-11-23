import { cx } from '../../lib/cx'

export interface AvatarProps {
  /** Full name — initials are derived from it. */
  name: string
  className?: string
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

/** User avatar with derived initials (`.avatar`). */
export function Avatar({ name, className }: AvatarProps) {
  return (
    <div className={cx('avatar', className)} aria-hidden="true">
      {initials(name)}
    </div>
  )
}
