import type { InputHTMLAttributes } from 'react'
import { useId, useState } from 'react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { Eye, EyeSlash, WarningCircle } from '@phosphor-icons/react'
import { cx } from '../../lib/cx'
import { Icon } from './Icon'

export interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
  /** Leading icon inside the input (`.field-wrap` + `.field-icon`). */
  icon?: PhosphorIcon
  /** Error message — sets the error border and shows `.field-msg`. */
  error?: string
  /**
   * Drop the reveal toggle from a password input. Here for the field where
   * showing the characters would be wrong (a shared screen, a recording) — the
   * toggle is the default, because typing a password blind is how people end up
   * locked out of an account whose password they know.
   */
  noReveal?: boolean
}

/** Text input with label, optional leading icon and inline error (`.field`). */
export function Field({ label, icon, error, className, id, type, noReveal, ...rest }: FieldProps) {
  const autoId = useId()
  const inputId = id ?? autoId
  const [revealed, setRevealed] = useState(false)

  // Revealing swaps the input's `type`, so while hidden the browser's own
  // password handling (managers, autofill, no spellcheck) is left intact.
  const isPassword = type === 'password' && !noReveal
  const effectiveType = isPassword && revealed ? 'text' : type

  const input = (
    <input
      id={inputId}
      type={effectiveType}
      className={cx('field', isPassword && 'field-has-action', error && 'field-error', className)}
      aria-invalid={error ? true : undefined}
      {...rest}
    />
  )

  return (
    <div className="field-group">
      {label && (
        <label className="field-label" htmlFor={inputId}>
          {label}
        </label>
      )}
      {icon || isPassword ? (
        <div className="field-wrap">
          {icon && <Icon icon={icon} size={16} className="field-icon" />}
          {input}
          {isPassword && (
            <button
              type="button"
              className="field-action"
              onClick={() => setRevealed((v) => !v)}
              aria-label={revealed ? 'Ocultar contraseña' : 'Mostrar contraseña'}
              aria-pressed={revealed}
              title={revealed ? 'Ocultar contraseña' : 'Mostrar contraseña'}
              // Out of the tab order on purpose: someone who just typed a
              // password and presses Tab means to reach the submit button, not
              // to put the password on screen.
              tabIndex={-1}
            >
              <Icon icon={revealed ? EyeSlash : Eye} size={16} />
            </button>
          )}
        </div>
      ) : (
        input
      )}
      {error && (
        <div className="field-msg">
          <Icon icon={WarningCircle} size="sm" />
          {error}
        </div>
      )}
    </div>
  )
}
