import { cx } from '../../lib/cx'

export interface SegmentedOption<T extends string> {
  value: T
  label: string
  /** Shown but not selectable — e.g. a mode the plan does not include. */
  disabled?: boolean
  /** Tooltip; says why when the option is disabled. */
  title?: string
}

export interface SegmentedProps<T extends string> {
  options: SegmentedOption<T>[]
  value: T
  onChange: (value: T) => void
  className?: string
  'aria-label'?: string
}

/**
 * Segmented control (`.segmented`) — e.g. Monthly/Annual, USD/ARS.
 * The instant-recalc interaction pattern reused across pricing and the ledger.
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  className,
  'aria-label': ariaLabel,
}: SegmentedProps<T>) {
  return (
    <div className={cx('segmented', className)} role="tablist" aria-label={ariaLabel}>
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          role="tab"
          aria-selected={value === opt.value}
          aria-disabled={opt.disabled || undefined}
          disabled={opt.disabled}
          title={opt.title}
          className={cx(value === opt.value && 'active')}
          onClick={() => !opt.disabled && onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
