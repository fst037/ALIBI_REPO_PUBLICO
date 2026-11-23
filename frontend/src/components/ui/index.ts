/* Barrel for the ALIBI UI primitives (built on tokens.css / alibi.css). */

export { AlibiMark, AlibiLogo } from './Logo'
export type { AlibiMarkProps, AlibiLogoProps } from './Logo'

export { Icon } from './Icon'
export type { AppIconProps, IconSize } from './Icon'

export { Button } from './Button'
export type { ButtonProps, ButtonVariant, ButtonSize } from './Button'

export { Pill } from './Pill'
export type { PillProps, PillTone } from './Pill'

export { Chip } from './Chip'
export type { ChipProps } from './Chip'

export { Eyebrow } from './Eyebrow'
export type { EyebrowProps } from './Eyebrow'

export { Avatar } from './Avatar'
export type { AvatarProps } from './Avatar'

export { Field } from './Field'
export type { FieldProps } from './Field'

export { Switch } from './Switch'
export type { SwitchProps } from './Switch'

export { Segmented } from './Segmented'
export type { SegmentedProps, SegmentedOption } from './Segmented'

export { CodeInput } from './CodeInput'
export type { CodeInputProps } from './CodeInput'

export { Card, CardHeading } from './Card'
export type { CardProps, CardTitleProps } from './Card'

export { DatasetCard, FILE_ICON } from './DatasetCard'
export type { DatasetCardProps, DatasetFileType, DatasetStatus } from './DatasetCard'

export { EmptyState } from './EmptyState'
export type { EmptyStateProps } from './EmptyState'

export { Modal } from './Modal'
export type { ModalProps } from './Modal'

export { DataTable, TableSkeleton } from './DataTable'
export type { DataTableProps, Column, TableSkeletonProps } from './DataTable'

export { Disclosure } from './Disclosure'
export type { DisclosureProps } from './Disclosure'

export { SqlBlock } from './SqlBlock'
export type { SqlBlockProps } from './SqlBlock'

export { ChartTypePicker, CHART_TYPES } from './ChartTypePicker'
export type { ChartType, ChartTypeMeta, ChartTypePickerProps } from './ChartTypePicker'

export { Skeleton } from './Skeleton'
export type { SkeletonProps } from './Skeleton'

export { Spinner } from './Spinner'
export { ThinkingDots } from './ThinkingDots'
export { LiveDot } from './LiveDot'

export { Toast } from './Toast'
export type { ToastProps, ToastTone } from './Toast'

export { UsageBar } from './UsageBar'
export type { UsageBarProps } from './UsageBar'

export { PlanCard } from './PlanCard'
export type { PlanCardProps } from './PlanCard'

/* Module C's renderer: a Vega-Lite spec, themed with tokens.css. */
export { VegaChart } from './charts/VegaChart'
export type { VegaChartProps } from './charts/VegaChart'
export { alibiVegaConfig, DATA_COLORS } from './charts/vegaTheme'

/* The pre-Module-C SVG charts (--data-* palette). No screen renders these any
   more: the per-dataset gallery that did was replaced by named dashboards, and
   those draw Vega-Lite specs. Kept as the Design System's charting primitives
   (see /dev/design-system) — but a chart pinned before 2026-08-07 has nowhere
   to be displayed. */
export { ChartRenderer } from './charts/ChartRenderer'
export type { ChartData, ChartRendererProps } from './charts/ChartRenderer'
export { BarChart } from './charts/BarChart'
export type { BarChartProps } from './charts/BarChart'
export { LineChart } from './charts/LineChart'
export type { LineChartProps } from './charts/LineChart'
export { Histogram } from './charts/Histogram'
export type { HistogramProps } from './charts/Histogram'
export { ScatterChart } from './charts/ScatterChart'
export type { ScatterChartProps } from './charts/ScatterChart'
export { PieChart } from './charts/PieChart'
export type { PieChartProps } from './charts/PieChart'
export { Heatmap } from './charts/Heatmap'
export type { HeatmapProps } from './charts/Heatmap'
export type { CategoricalDatum, ScatterPoint } from './charts/chartCore'
