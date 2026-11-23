import { cx } from '../../lib/cx'

export interface UsageBarProps {
  value: number
  max: number
  /** Force a tone; otherwise derived from the ratio (warn ≥0.8, danger ≥0.95). */
  tone?: 'default' | 'warn' | 'danger'
  className?: string
}

/** Plan-usage / progress bar (`.usage`). */
export function UsageBar({ value, max, tone, className }: UsageBarProps) {
  const ratio = max > 0 ? Math.min(value / max, 1) : 0
  const derived = tone ?? (ratio >= 0.95 ? 'danger' : ratio >= 0.8 ? 'warn' : 'default')
  return (
    <div className={cx('usage', className)}>
      <div className="usage-track">
        <div
          className={cx('usage-fill', derived !== 'default' && derived)}
          style={{ width: `${ratio * 100}%` }}
          role="progressbar"
          aria-valuenow={value}
          aria-valuemin={0}
          aria-valuemax={max}
        />
      </div>
    </div>
  )
}
