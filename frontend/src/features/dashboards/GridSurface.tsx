import type { ReactNode } from 'react'
import { ResponsiveGridLayout, useContainerWidth } from 'react-grid-layout'
import type { Layout } from 'react-grid-layout'
import type { ChartLayout } from '../../types'
import styles from './dashboards.module.css'

export interface GridSurfaceProps {
  /** `chartId → position`, in the `lg` breakpoint's column space. */
  items: (ChartLayout & { id: string })[]
  onArrange: (layout: Record<string, ChartLayout>) => void
  children: ReactNode
}

/**
 * Columns per breakpoint; one card wide on a phone regardless.
 *
 * `lg` is 24, not 12 — and has to match `GRID_COLUMNS` in the backend's
 * `app/schemas/dashboards.py` exactly, since only `lg` positions persist
 * (narrower breakpoints are derived here, client-side, and never saved). At
 * 12 columns the smallest chart that still looked deliberate was already half
 * the row, so three could never sit side by side without a resize aggressive
 * enough to stop looking chosen. Doubling the resolution is what makes both
 * "three per row" and finer steps in between possible at the same time.
 */
const COLS = { lg: 24, md: 8, sm: 4, xs: 2, xxs: 1 }
const BREAKPOINTS = { lg: 1200, md: 900, sm: 640, xs: 460, xxs: 0 }

// The smallest a chart can be dragged down to — a sixth of the row, so up to
// six can share one line. Purely a client-side affordance: the API only
// requires `w >= 1` (`ChartLayout.w` in the backend schema), this is the
// floor past which a chart stops being worth looking at.
const MIN_CHART_W = COLS.lg / 6
const MIN_CHART_H = 3

/**
 * The react-grid-layout surface, in its own module so the library lands in a
 * lazy chunk (see `DashboardGrid`) — it is ~35 kB that only this screen needs.
 *
 * v2 wants an explicit pixel `width` rather than the v1 `WidthProvider` HOC, so
 * `useContainerWidth` measures the wrapper with a ResizeObserver. `mounted`
 * gates the first render: laying out against the hook's default 1280 and then
 * correcting would show every card jumping on open.
 */
export function GridSurface({ items, onArrange, children }: GridSurfaceProps) {
  const { width, containerRef, mounted } = useContainerWidth()

  const layouts = {
    lg: items.map((item) => ({
      i: item.id,
      x: item.x,
      y: item.y,
      w: item.w,
      h: item.h,
      minW: MIN_CHART_W,
      minH: MIN_CHART_H,
    })),
  }

  // Persisted from drag/resize *stop*, never from `onLayoutChange`: that one
  // also fires on mount and on every breakpoint switch, so saving from it would
  // rewrite the arrangement the user chose with whatever the current window
  // width implied — a board rearranged by resizing the browser.
  const persist = (layout: Layout) => {
    const next: Record<string, ChartLayout> = {}
    for (const item of layout) next[item.i] = { x: item.x, y: item.y, w: item.w, h: item.h }
    onArrange(next)
  }

  return (
    <div ref={containerRef}>
      {mounted && (
        <ResponsiveGridLayout
          className={styles.board}
          width={width}
          layouts={layouts}
          breakpoints={BREAKPOINTS}
          cols={COLS}
          rowHeight={78}
          margin={[16, 16]}
          containerPadding={[0, 0]}
          dragConfig={{
            // No `handle`: `LiveChartCard` has no header row any more, so the
            // whole card — chart included — is the drag surface. A plain
            // click still reaches its target underneath: react-draggable only
            // starts a drag past a few pixels of pointer movement, so this
            // doesn't break clicking the "Try again" button on a failed card.
            // `cancel` excludes only the two floating buttons and the
            // question popover, which sit on top of the chart and must move
            // (or open) on their own click rather than drag the card.
            cancel: `.${styles.liveFloatBtns}, .${styles.liveQ}`,
          }}
          // Only `lg` positions are stored. Narrower breakpoints are derived by
          // the library and deliberately not saved: a board arranged on a
          // laptop must not be rewritten because someone opened it on a phone.
          onDragStop={persist}
          onResizeStop={persist}
        >
          {children}
        </ResponsiveGridLayout>
      )}
    </div>
  )
}
