import { useEffect, useMemo, useRef, useState } from 'react'
import type { View } from 'vega'
import type { ResultTable } from '../../../types'
import { alibiVegaConfig } from './vegaTheme'
import { Skeleton } from '../Skeleton'

export interface VegaChartProps {
  /** Module C's Vega-Lite v5 document — **without** a `data` key. */
  spec: Record<string, unknown>
  /** The rows the spec describes. Attached here; never sent twice over the wire. */
  table: ResultTable
  height?: number
  /**
   * Fill the parent's height instead of taking a fixed one. For a dashboard
   * card, whose height is whatever the user dragged the grid row to — a fixed
   * pixel height there would either overflow the card or leave a gap under it.
   * The parent must have a definite height of its own, which in the grid it
   * does.
   */
  fill?: boolean
  /** Accessible description; falls back to the spec's own title. */
  label?: string
}

/**
 * The results table as Vega-Lite wants it.
 *
 * One substitution matters: the backend writes `"—"` for a null cell, because
 * `ResultTable.rows` holds strings and numbers and that glyph is what the
 * DataTable shows for an absent value. Handing that em-dash to a quantitative
 * encoding turns the whole column into text and silently drops every point, so
 * it goes back to `null` — which Vega-Lite understands as a gap and draws as one.
 */
function toValues(table: ResultTable): Record<string, unknown>[] {
  return table.rows.map((row) => {
    const out: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(row)) out[key] = value === '—' ? null : value
    return out
  })
}

/**
 * Renders a Vega-Lite specification with the ALIBI theme applied.
 *
 * This is where Module C's output becomes a picture. The spec arrives validated
 * server-side against the same `vega-lite` version this renders with (the
 * backend's schema is copied from this package — see
 * `backend/app/orchestrator/vega.py`), so a spec that got here renders; the
 * error branch below is for a bad *dataset*, not a bad spec.
 *
 * `vega-embed` is imported dynamically. It pulls in the whole Vega runtime,
 * which is by a wide margin the largest thing in this app, and most screens
 * never draw a chart — loading it with the route would make the login page pay
 * for the visualization module.
 */
export function VegaChart({ spec, table, height = 300, fill = false, label }: VegaChartProps) {
  const host = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)

  // The effect keys off the spec's *content*, not its identity. Every caller
  // today passes a stable object straight from props, but this is a shared
  // primitive and one that builds a spec inline would otherwise get an infinite
  // loop: a new object each render re-runs the effect, the effect's `setReady`
  // causes a render, and the only symptom is a chart that flickers forever.
  const specKey = useMemo(() => JSON.stringify(spec), [spec])
  const latest = useRef(spec)
  latest.current = spec

  useEffect(() => {
    let view: View | null = null
    let observer: ResizeObserver | null = null
    let cancelled = false
    const el = host.current
    if (!el) return

    setError(null)
    setReady(false)

    void (async () => {
      try {
        const { default: embed } = await import('vega-embed')
        if (cancelled || !host.current) return
        const result = await embed(
          host.current,
          {
            ...latest.current,
            // `container` makes the chart track the answer card's width, which
            // changes with the sidebar and in fullscreen. The spec never sets
            // its own size (prompt rule 8), so there is nothing to override.
            width: 'container',
            height: fill ? 'container' : height,
            data: { values: toValues(table) },
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
          } as any,
          {
            actions: false,
            renderer: 'svg',
            config: alibiVegaConfig,
            tooltip: { theme: 'custom' },
          },
        )
        if (cancelled) {
          result.finalize()
          return
        }
        view = result.view
        setReady(true)

        // Vega-Lite's own `"container"` autosize only re-measures on the
        // browser window's `resize` event — see `sizeSignals` in
        // vega-lite/build/vega-lite.js, which ties the width/height signal to
        // `events: 'window:resize'` and nothing else. That event never fires
        // when only this element's box changes: a sidebar collapsing, or —
        // the case that actually surfaced this — a dashboard card being
        // dragged to a new size. The chart would keep the size it was born
        // at, now clipped or gapped inside a container of a different shape.
        //
        // Driving the view's width/height from a `ResizeObserver` on the same
        // element is the fix: it fires on the container's actual size
        // changes, including the first layout, so it fully replaces the
        // window-resize path rather than supplementing it.
        observer = new ResizeObserver((entries) => {
          const box = entries[0]?.contentRect
          if (!box || box.width <= 0 || box.height <= 0 || !view) return
          view.width(box.width)
          // Only in fill mode is height container-derived; a fixed `height`
          // prop is already the number embed was given, and re-deriving it
          // from the observed box would fight that value instead of using it.
          if (fill) view.height(box.height)
          view.run()
        })
        observer.observe(el)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      }
    })()

    return () => {
      cancelled = true
      observer?.disconnect()
      view?.finalize()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `specKey` stands in for `spec`; see above.
  }, [specKey, table, height, fill])

  if (error) {
    return (
      <p className="hint" style={{ padding: 20 }} role="status">
        No se pudo dibujar este resultado.
      </p>
    )
  }

  return (
    // `width: 100%` and `minWidth: 0` are not cosmetic. Vega sizes the chart
    // from the host element's measured width (`width: 'container'`), so in a
    // flex parent — which the fullscreen overlay's `.fsChart` is — a wrapper
    // with auto width resolves from its *content*, the content resolves from
    // the wrapper, and the chart renders at zero width: a blank panel with no
    // error anywhere. The hand-written SVG charts never hit this because a
    // viewBox gives them an intrinsic aspect ratio to be sized by.
    <div
      style={{
        position: 'relative',
        width: '100%',
        minWidth: 0,
        ...(fill ? { height: '100%' } : { minHeight: height }),
      }}
    >
      {!ready && (
        <div style={{ position: 'absolute', inset: 0 }} aria-hidden>
          <Skeleton variant="block" height={fill ? '100%' : height} />
        </div>
      )}
      <div
        ref={host}
        role="img"
        aria-label={label ?? (typeof spec.title === 'string' ? spec.title : 'Gráfico')}
        style={{
          width: '100%',
          // `height: 'container'` measures this element, so in fill mode it
          // needs a definite height of its own — the same container/content
          // circularity the width comment above describes, on the other axis.
          ...(fill ? { height: '100%' } : {}),
          opacity: ready ? 1 : 0,
          transition: 'opacity var(--dur) var(--ease)',
        }}
      />
    </div>
  )
}
