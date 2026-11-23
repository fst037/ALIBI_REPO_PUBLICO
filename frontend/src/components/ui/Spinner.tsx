import { cx } from '../../lib/cx'

/** Small inline spinner (`.spinner`) — for buttons/inline loading only. */
export function Spinner({ className }: { className?: string }) {
  return <span className={cx('spinner', className)} role="status" aria-label="Cargando" />
}
