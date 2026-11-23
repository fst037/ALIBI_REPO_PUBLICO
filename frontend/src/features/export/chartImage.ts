import type { ChartSpec, ResultTable } from '../../types'
import { alibiVegaConfig, alibiVegaPrintConfig } from '../../components/ui/charts/vegaTheme'

/** The canvas colour of the app (`--bg`); exports are opaque, the on-screen theme is not. */
const EXPORT_BACKGROUND = '#0a0a0c'
/** The PDF report is a white page. */
const PRINT_BACKGROUND = '#ffffff'

export interface ChartImage {
  dataUrl: string
  width: number
  height: number
}

/**
 * Draws Module C's spec off-screen and returns it as a PNG.
 *
 * The on-screen chart sizes itself from its container (`width: 'container'`),
 * which does not exist for a detached node, so the export fixes a size. The
 * on-screen theme has a transparent background for the glass card; a PNG needs
 * an opaque one or the light-on-dark text disappears on a white viewer.
 * `vega-embed` is imported dynamically for the same reason as in `VegaChart`.
 */
export async function chartToPng(
  chart: ChartSpec,
  table: ResultTable,
  scale = 2,
  theme: 'screen' | 'print' = 'screen',
): Promise<ChartImage> {
  const width = 960
  const height = 480
  const { default: embed } = await import('vega-embed')
  const host = document.createElement('div')
  host.style.cssText = 'position:fixed;left:-10000px;top:0;pointer-events:none'
  document.body.appendChild(host)
  try {
    const result = await embed(
      host,
      {
        ...chart.spec,
        width,
        height,
        data: {
          values: table.rows.map((row) => {
            const out: Record<string, unknown> = {}
            for (const [k, v] of Object.entries(row)) out[k] = v === '—' ? null : v
            return out
          }),
        },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any,
      {
        actions: false,
        renderer: 'canvas',
        config:
          theme === 'print'
            ? { ...alibiVegaPrintConfig, background: PRINT_BACKGROUND }
            : { ...alibiVegaConfig, background: EXPORT_BACKGROUND },
      },
    )
    const dataUrl = await result.view.toImageURL('png', scale)
    result.finalize()
    // Autosize pads the plot with axes and legend, so read the real size back.
    const img = new Image()
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve()
      img.onerror = () => reject(new Error('No se pudo leer la imagen del gráfico.'))
      img.src = dataUrl
    })
    return { dataUrl, width: img.naturalWidth / scale, height: img.naturalHeight / scale }
  } finally {
    host.remove()
  }
}
