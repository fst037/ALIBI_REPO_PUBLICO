import type { ChartType } from '../ChartTypePicker'
import type { CategoricalDatum, ScatterPoint } from './chartCore'
import { BarChart } from './BarChart'
import { LineChart } from './LineChart'
import { Histogram } from './Histogram'
import { PieChart } from './PieChart'
import { ScatterChart } from './ScatterChart'
import { Heatmap } from './Heatmap'

/** Normalized chart payload — the shape depends on the chart family. */
export type ChartData =
  | { kind: 'categorical'; data: CategoricalDatum[] }
  | { kind: 'scatter'; points: ScatterPoint[] }
  | { kind: 'heatmap'; rows: string[]; cols: string[]; values: number[][]; normalize?: 'global' | 'column' }

export interface ChartRendererProps {
  type: ChartType
  data: ChartData
  color?: string
}

function Mismatch({ type }: { type: ChartType }) {
  return (
    <p className="hint" style={{ padding: 20 }}>
      Estos datos no se pueden mostrar como gráfico «{type}».
    </p>
  )
}

/**
 * Renders the SVG chart for a given type. `tabla` returns null — the caller
 * shows the results DataTable instead (see flujo 3.3).
 */
export function ChartRenderer({ type, data, color }: ChartRendererProps) {
  switch (type) {
    case 'bar':
      return data.kind === 'categorical' ? <BarChart data={data.data} color={color} /> : <Mismatch type={type} />
    case 'line':
      return data.kind === 'categorical' ? <LineChart data={data.data} color={color} /> : <Mismatch type={type} />
    case 'histogram':
      return data.kind === 'categorical' ? <Histogram bins={data.data} color={color} /> : <Mismatch type={type} />
    case 'pie':
      return data.kind === 'categorical' ? <PieChart data={data.data} /> : <Mismatch type={type} />
    case 'scatter':
      return data.kind === 'scatter' ? <ScatterChart points={data.points} color={color} /> : <Mismatch type={type} />
    case 'heatmap':
      return data.kind === 'heatmap' ? (
        <Heatmap rows={data.rows} cols={data.cols} values={data.values} normalize={data.normalize} />
      ) : (
        <Mismatch type={type} />
      )
    case 'table':
      return null
  }
}
