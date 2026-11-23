import {
  ChartBar,
  ChartBarHorizontal,
  ChartLine,
  ChartPie,
  ChartScatter,
  GridNine,
} from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { cx } from '../../lib/cx'
import { Icon } from './Icon'

/** The 7 supported chart types. One is auto-selected; the user can switch. */
export type ChartType = 'bar' | 'line' | 'scatter' | 'pie' | 'histogram' | 'heatmap' | 'table'

export interface ChartTypeMeta {
  key: ChartType
  label: string
  icon: PhosphorIcon
}

export const CHART_TYPES: ChartTypeMeta[] = [
  { key: 'bar', label: 'Barras', icon: ChartBar },
  { key: 'line', label: 'Líneas', icon: ChartLine },
  { key: 'scatter', label: 'Dispersión', icon: ChartScatter },
  { key: 'pie', label: 'Torta', icon: ChartPie },
  { key: 'histogram', label: 'Histograma', icon: ChartBarHorizontal },
  { key: 'heatmap', label: 'Mapa de calor', icon: GridNine },
]

export interface ChartTypePickerProps {
  value: ChartType
  onChange: (type: ChartType) => void
  /** Which type the system auto-selected — gets the "auto" tag. */
  auto?: ChartType
}

/**
 * Chart-type selector (`.chart-picker`).
 *
 * **The answer flow deliberately does not use this**, and neither does the
 * dashboard: Module C picks the chart from the question and the data, and
 * offering an override invites the user to overrule a choice they have less
 * information to make. It survives as a
 * primitive — the Design System screen catalogues it — and `CHART_TYPES` below
 * is still the icon table History renders from.
 */
export function ChartTypePicker({ value, onChange, auto }: ChartTypePickerProps) {
  return (
    <div className="chart-picker" role="radiogroup" aria-label="Tipo de gráfico">
      {CHART_TYPES.map((t) => (
        <button
          key={t.key}
          type="button"
          role="radio"
          aria-checked={value === t.key}
          className={cx('chart-opt', value === t.key && 'active', auto === t.key && 'auto-tag')}
          onClick={() => onChange(t.key)}
        >
          <Icon icon={t.icon} size="sm" />
          {t.label}
        </button>
      ))}
    </div>
  )
}
