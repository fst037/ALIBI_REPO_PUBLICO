import { cx } from '../../lib/cx'

export interface SwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  /** Accessible label — also renders visibly when `showLabel`. */
  label?: string
  showLabel?: boolean
  disabled?: boolean
  className?: string
}

/** Two-state toggle (`.switch`). Rendered as a real button with role=switch. */
export function Switch({
  checked,
  onChange,
  label,
  showLabel = false,
  disabled = false,
  className,
}: SwitchProps) {
  const toggle = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={!showLabel ? label : undefined}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx('switch', checked && 'on', className)}
    />
  )
  if (showLabel && label) {
    return (
      <div className="row" style={{ gap: 9 }}>
        {toggle}
        <span className="hint">{label}</span>
      </div>
    )
  }
  return toggle
}
