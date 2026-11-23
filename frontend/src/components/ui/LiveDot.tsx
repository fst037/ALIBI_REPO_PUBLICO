import { cx } from '../../lib/cx'

/** Pulsing activity dot (`.live-dot`) — running query / processing. */
export function LiveDot({ className }: { className?: string }) {
  return <span className={cx('live-dot', className)} aria-hidden="true" />
}
