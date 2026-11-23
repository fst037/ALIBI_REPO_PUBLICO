import type { ReactNode } from 'react'
import { Check } from '@phosphor-icons/react'
import { cx } from '../../lib/cx'
import { Icon } from './Icon'

export interface PlanCardProps {
  name: string
  price: ReactNode
  /** e.g. "/mo". */
  per?: ReactNode
  /** Optional one-line description under the price. */
  tagline?: ReactNode
  features: string[]
  featured?: boolean
  /** Subtle badge, e.g. "Recommended". */
  badge?: string
  /** CTA button(s). */
  cta: ReactNode
  className?: string
}

/** Pricing / plan card (`.plan`). No Module D features ever appear here. */
export function PlanCard({ name, price, per, tagline, features, featured = false, badge, cta, className }: PlanCardProps) {
  return (
    <div className={cx('plan', featured && 'featured', className)}>
      {badge && (
        <span className="pill pill-accent" style={{ position: 'absolute', top: -11, left: 26 }}>
          {badge}
        </span>
      )}
      <div className="plan-name" style={featured ? { color: 'var(--accent)' } : undefined}>
        {name}
      </div>
      <div className="plan-price">
        <span className="amt">{price}</span>
        {per && <span className="per">{per}</span>}
      </div>
      {tagline && <p className="hint" style={{ margin: '-8px 0 0' }}>{tagline}</p>}
      <ul className="plan-feats">
        {features.map((f, i) => (
          <li key={i}>
            <Icon icon={Check} size="sm" className="i" />
            {f}
          </li>
        ))}
      </ul>
      {cta}
    </div>
  )
}
