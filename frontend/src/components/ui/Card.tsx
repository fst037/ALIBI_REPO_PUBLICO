import type { HTMLAttributes, ReactNode } from 'react'
import { cx } from '../../lib/cx'

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Stronger glass fill (`.card-strong`). */
  strong?: boolean
  children: ReactNode
}

/** Padded glass surface (`.card`). */
export function Card({ strong = false, className, children, ...rest }: CardProps) {
  return (
    <div className={cx('card', strong && 'card-strong', className)} {...rest}>
      {children}
    </div>
  )
}

export interface CardTitleProps {
  title: ReactNode
  sub?: ReactNode
}

/** Optional title/subtitle pair (`.card-title` / `.card-sub`). */
export function CardHeading({ title, sub }: CardTitleProps) {
  return (
    <div>
      <h3 className="card-title">{title}</h3>
      {sub && <p className="card-sub">{sub}</p>}
    </div>
  )
}
