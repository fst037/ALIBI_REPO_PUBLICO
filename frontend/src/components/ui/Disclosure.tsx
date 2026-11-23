import type { ReactNode } from 'react'
import { useState } from 'react'
import { CaretRight } from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { cx } from '../../lib/cx'
import { Icon } from './Icon'

export interface DisclosureProps {
  label: ReactNode
  icon?: PhosphorIcon
  /** Collapsed by default — the reason this component exists. */
  defaultOpen?: boolean
  /** Rendered in the header, right of the label. Clicks don't toggle. */
  actions?: ReactNode
  /** Rendered under the header when open. */
  children: ReactNode
  /** Class for the body wrapper (`.sql-body` gives the SQL panel its mono type). */
  bodyClassName?: string
}

/**
 * A collapsible panel: header with a caret, content hidden until asked for.
 *
 * The chat answer has two of these now — the generated SQL and the results
 * table — and they have to look like the same control, because they are the
 * same offer: "the detail is here if you want it". This owns that chrome so
 * there is one implementation of the open/closed state, the keyboard handling
 * and the caret rotation.
 *
 * The classes are still named `sql-*` in `alibi.css`. They came from the
 * prototype under that name and only `.sql-body` and the token colours are
 * actually SQL-specific; renaming the rest would be a diff across the
 * stylesheet for no behaviour.
 */
export function Disclosure({
  label,
  icon,
  defaultOpen = false,
  actions,
  children,
  bodyClassName,
}: DisclosureProps) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="sql-block">
      <div
        className={cx('sql-head', open && 'open')}
        onClick={() => setOpen((v) => !v)}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setOpen((v) => !v)
          }
        }}
      >
        <div className="sql-label">
          {icon && <Icon icon={icon} size="sm" />}
          {label}
        </div>
        <div className="row" style={{ gap: 8 }}>
          {actions}
          <Icon icon={CaretRight} size="sm" className={cx('sql-caret', open && 'open')} />
        </div>
      </div>
      {open && <div className={bodyClassName}>{children}</div>}
    </div>
  )
}
