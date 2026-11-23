/* ============================================================
   ALIBI — domain model
   These types describe the product entities. The `lib/api` layer returns these shapes;
   screens consume them via lib/api only, never touching mocks directly.
   Module D (AutoML) has no representation here — by design.
   ============================================================ */

import type { ChartType } from '../components/ui/ChartTypePicker'
import type { ChartData } from '../components/ui/charts/ChartRenderer'
import type { DatasetFileType } from '../components/ui/DatasetCard'

export type { ChartType, ChartData, DatasetFileType }

/* ---- User / workspace ---- */

export interface User {
  id: string
  name: string
  email: string
  workspace: string
}

/* ---- Plans / subscription ---- */

export type PlanId = 'free' | 'pro'
export type BillingCycle = 'monthly' | 'annual'
export type ExportFormat = 'csv' | 'png' | 'pdf'

/** Quick or Deep — the same values as the API's `RunMode`. Deep is Pro only. */
export type AnswerMode = 'quick' | 'deep'

export interface PlanLimits {
  /** null = unlimited / "no practical limit". */
  datasets: number | null
  queriesPerMonth: number | null
  rowsPerQuery: number | null
  /** Largest file one upload may be, in MB. */
  maxFileMb: number
  /** How far back History reaches; null = unlimited. */
  historyDays: number | null
  exportFormats: ExportFormat[]
  /** Answer depths the plan includes: Free ['quick'], Pro ['quick', 'deep']. */
  answerModes: AnswerMode[]
}

/**
 * A plan of the business model: Free and Pro, charged in ARS, VAT excluded.
 * There is no third plan — past Pro's quota the user pays per query
 * (`overagePricePerQuery`).
 *
 * Every price is tied to the dollar: the backend holds a USD reference value
 * (`*Usd`) and returns the peso price at the official rate (`usdArsRate`), so
 * screens show the pesos as given and never compute them.
 */
export interface Plan {
  id: PlanId
  name: string
  /** ISO 4217 — 'ARS'. The peso prices are in it, VAT excluded. */
  currency: string
  /** Per month, billed monthly, in pesos. */
  priceMonthly: number
  /** Per month, billed annually in advance (the discounted rate), in pesos. */
  priceAnnual: number
  /** One query beyond `limits.queriesPerMonth`, in pesos; null = no overage (Free). */
  overagePricePerQuery: number | null
  /** USD reference values of the three prices above. */
  priceMonthlyUsd: number
  priceAnnualUsd: number
  overagePricePerQueryUsd: number | null
  /** Official ARS per USD the peso prices were computed at, and its date (ISO). */
  usdArsRate: number
  usdArsRateAsOf: string
  recommended: boolean
  features: string[]
  limits: PlanLimits
}

export interface UsageMetric {
  used: number
  /** null = unbounded. */
  limit: number | null
  label: string
}

export interface PaymentMethod {
  brand: string
  last4: string
  expiry: string
}

export interface Invoice {
  id: string
  date: string
  amount: number
  status: 'paid' | 'pending' | 'failed'
}

/** Pay-per-query past the monthly quota (Pro). `queries`/`amountArs` are this month's. */
export interface Overage {
  /** False on a plan without overage (Free). */
  available: boolean
  enabled: boolean
  /** Most the user accepts to be billed for overage in a month; null = no cap. */
  capArs: number | null
  pricePerQueryArs: number | null
  queries: number
  amountArs: number
}

export interface Subscription {
  planId: PlanId
  cycle: BillingCycle
  usage: {
    queries: UsageMetric
    datasets: UsageMetric
    rowsPerQuery: UsageMetric
  }
  overage: Overage
  paymentMethod: PaymentMethod | null
  invoices: Invoice[]
}

/* ---- Datasets & inferred schema ---- */

export type DatasetStatus =
  | 'processed'
  | 'processing'
  /** Table materialised and previewable, but not queryable until the user
   *  confirms the inferred schema (Ingesta 1.2). */
  | 'awaiting_confirmation'
  | 'error'

export interface Dataset {
  id: string
  name: string
  fileType: DatasetFileType
  /** Row count (tabular) — undefined for PDFs, which use `pages`. */
  rows?: number
  pages?: number
  /** Inferred column count (tabular). */
  columns?: number
  sizeLabel?: string
  /** Prebuilt secondary line, e.g. "12,480 rows · 3 days ago". */
  meta: string
  favorite: boolean
  recent: boolean
  status: DatasetStatus
  createdAt: string
  updatedAt: string
  /** SQL identifier Modules A and C put in the FROM clause. */
  tableName?: string
  /** The upload this table was found in; several datasets can share one. */
  uploadId?: string
  /** Where inside the file it came from ("Sheet “Sales” · table 2"). */
  sourceLabel?: string
  /** Folders this dataset is linked into — a mapping, never a copy. */
  folderIds?: string[]
}

export type ColumnType = 'Text' | 'Number' | 'Whole number' | 'Date' | 'Percentage' | 'Boolean'
export type InferenceConfidence = 'high' | 'medium' | 'low'

export interface SchemaColumn {
  name: string
  type: ColumnType
  /** Example values, e.g. "Buenos Aires, Córdoba…". */
  sampleValues: string
  included: boolean
  confidence: InferenceConfidence
  /** Raw header from the file, present only when inference renamed the column. */
  sourceName?: string
  /** Type hint Module C uses when choosing an encoding. */
  vlType?: 'quantitative' | 'nominal' | 'ordinal' | 'temporal'
  description?: string
  isPrimaryKey?: boolean
  /** ETLite, sent on confirm/preview only — applied to the data, never read back. */
  cleaning?: ColumnCleaning | null
}

export interface DatasetSchema {
  datasetId: string
  /** Rows in the stored table (after ETLite's cleaning, once confirmed with one). */
  rowCount: number
  confidence: InferenceConfidence
  columns: SchemaColumn[]
  /** One-sentence summary of the table (semantic inference). */
  tableDescription?: string
  confirmed?: boolean
}

/* ---- ETLite: light cleaning chosen at confirmation ---- */

/** What happens to a column's empty cells. `mode` = the most frequent value. */
export type NullStrategy = 'keep' | 'drop_row' | 'zero' | 'mean' | 'median' | 'mode' | 'value'
export type TextCase = 'keep' | 'upper' | 'lower'

/** Previewed while confirming; on confirm, the cleaned table replaces the stored one. */
export interface ColumnCleaning {
  nulls?: NullStrategy
  /** For `value`, what the user typed; otherwise the literal the server resolved. */
  fillValue?: string | null
  /** Collapse stray and repeated whitespace. Text only. */
  trim?: boolean
  case?: TextCase
  /** Values that mean "no data" in this file, compared ignoring case. Text only. */
  nullTokens?: string[]
  /** Round to this many decimals. Decimal numbers only. */
  decimals?: number | null
}

export type DuplicateStrategy = 'keep' | 'exact' | 'by_columns'

export interface TableCleaning {
  duplicates?: DuplicateStrategy
  /** Ordinals into the schema's column list. */
  duplicateColumns?: number[]
}

/** Family of a column's physical type — decides which operations apply. */
export type ColumnKind = 'integer' | 'number' | 'text' | 'date' | 'boolean' | 'other'

export interface ColumnQuality {
  ordinal: number
  kind: ColumnKind
  nullCount: number
  /** Text only: cells that trimming would change. */
  whitespaceCount?: number
}

/** The stored table before any cleaning — what ETLite could fix. */
export interface DatasetQuality {
  datasetId: string
  rowCount: number
  duplicateRows: number
  columns: ColumnQuality[]
}

export interface ResolvedFill {
  ordinal: number
  value: string
}

export interface CleaningPreview {
  rowCount: number
  removedRows: number
  fills?: ResolvedFill[]
  preview: TablePreview
}

/* ---- Updating a dataset with a new file ---- */

export type UpdateMode = 'append' | 'replace'
/** On append: incoming rows identical to a stored one are kept or skipped. */
export type OverlapStrategy = 'keep_all' | 'skip'

export interface ColumnRecipe {
  ordinal: number
  cleaning: ColumnCleaning
}

/** ETLite's recipe as a dataset stores it, by column ordinal. */
export interface CleaningRecipe {
  table?: TableCleaning | null
  columns?: ColumnRecipe[]
}

export interface StructureDifference {
  kind: 'missing' | 'extra' | 'type' | 'ambiguous'
  column: string
  expected?: string
  found?: string
}

export interface UpdateTarget {
  datasetId: string
  name: string
  rows?: number
  compatible: boolean
  differences?: StructureDifference[]
}

export interface DatasetUpdateRequest {
  stagedDatasetId: string
  mode: UpdateMode
  onOverlap?: OverlapStrategy
  cleaning?: CleaningRecipe | null
}

export interface DatasetUpdatePreview {
  compatible: boolean
  differences?: StructureDifference[]
  existingRows?: number
  incomingRows?: number
  overlapRows?: number
  resultRows?: number
  storedCleaning?: CleaningRecipe | null
  fills?: ResolvedFill[]
}

/* ---- Ingestion: uploads & folders (Module B) ---- */

/** A file's lifecycle. One upload can yield SEVERAL datasets. */
export type UploadStatus = 'pending' | 'uploaded' | 'processing' | 'ready' | 'error'

/** Sub-step within `processing` — drives the stepper in Ingesta. */
export type UploadPhase = 'uploading' | 'extracting' | 'profiling' | 'inferring' | 'storing' | 'done'

export interface Upload {
  id: string
  filename: string
  fileType: DatasetFileType
  sizeBytes: number
  sizeLabel?: string
  status: UploadStatus
  phase?: UploadPhase
  /** How many tables were found in the file — can be > 1. */
  detectedTables?: number
  /** One entry per detected table, each `awaiting_confirmation`. */
  datasets: Dataset[]
  /** Non-fatal notes to show before confirming ("we found 2 tables"). */
  warnings?: string[]
  /** Plain-language failure message when `status === 'error'`. */
  error?: string
  createdAt: string
  updatedAt?: string
}

/** A one-level "project" grouping datasets. There are no subfolders. */
export interface Folder {
  id: string
  name: string
  datasetCount: number
  createdAt: string
  updatedAt?: string
}

export interface FolderDetail extends Folder {
  datasets: Dataset[]
}

/** Deliberately identical to `ResultColumn`, including the `num` alignment
 *  vocabulary, so `toColumns` maps a preview and a query result alike. */
export interface PreviewColumn {
  key: string
  label: string
  align: 'left' | 'num'
}

/** First N rows of a stored table, read through DuckDB. */
export interface TablePreview {
  datasetId: string
  columns: PreviewColumn[]
  rows: Record<string, string | number | null>[]
  truncated?: boolean
}

/* ---- Conversation (one continuous thread per data source) ---- */

export interface Insight {
  label: string
  value: string
  delta: string
}

export interface ResultColumn {
  key: string
  label: string
  align: 'left' | 'num'
}

export interface ResultTable {
  columns: ResultColumn[]
  rows: Record<string, string | number>[]
}

/**
 * One result column as Module C was shown it.
 *
 * **Nothing in the UI reads this today.** It existed so the chart-type chips
 * could re-encode the spec locally; the chips are gone, because the model picks
 * the form and offering an override invites the user to overrule a choice they
 * have less information to make. It stays on the wire as the record of what the
 * model was given — a saved chart is a historical record, and the profile is
 * the part that explains why that chart was chosen. Drop it if that stops being
 * worth the bytes.
 *
 * No `ordinal`: it cannot be inferred from a result table (see the backend's
 * `orchestrator/profile.py`). The model may still write `"type": "ordinal"`
 * inside the spec, which is where that judgement belongs.
 */
export interface ChartColumn {
  /** The RAW column name — the key that appears in `ResultTable.rows`. */
  name: string
  dtype: 'quantitative' | 'temporal' | 'nominal'
  /** Distinct non-null values across the whole result, not just the sample. */
  cardinality: number
}

/**
 * Module C's answer: a Vega-Lite v5 specification for one result.
 *
 * **Carries no data.** `spec` has no `data` key; `VegaChart` attaches
 * `result.table.rows` at render time. The backend strips any `data` the model
 * supplied — those would be numbers nobody queried, drawn over the user's own.
 */
export interface ChartSpec {
  /** Which of the 6 chips. Never `table`: "no chart" is `chart: null`. */
  chartType: Exclude<ChartType, 'table'>
  /** The Vega-Lite v5 document, without `data`. */
  spec: Record<string, unknown>
  columns: ChartColumn[]
  source?: 'model' | 'model_retry'
}

/**
 * A single assistant answer: natural-language explanation + collapsible SQL +
 * results table + Module C's chart.
 *
 * `chart` is **optional and often absent**, and that is a designed state: the
 * model judged no chart adds anything, or none it proposed validated. Both are
 * `chart: null` and the answer is still complete. Nothing is substituted —
 * a chart type picked from row shapes is the deterministic classifier that was
 * removed rather than left to disagree with a real model.
 *
 * `autoChartType`/`chartData`/`chartTitle`/`insights` belonged to that
 * classifier and to the pre-Vega rendering union. They live on
 * `SavedChartResult` only, so charts saved before Module C still render.
 */
export interface QueryResult {
  naturalResponse: string
  sql: string
  table: ResultTable
  rowCount: number
  rowsLabel: string
  chart?: ChartSpec | null
  /**
   * Every finding, in order, when the agent harness found two or more.
   * Empty (or absent, on answers stored before the harness) for a one-finding
   * answer — which then reads exactly from the fields above, as it always did.
   */
  findings?: Finding[]
  /** The queries behind the answer, for "How I got here". */
  trail?: TrailStep[]
}

/** One result the user should see, with its own sentence. */
export interface Finding {
  text: string
  /** What the finding is about, as the planner asked it. */
  question: string
  sql: string
  table: ResultTable
  rowCount: number
  rowsLabel: string
  chart?: ChartSpec | null
}

/**
 * One step the agent harness took — a query (context or insight, failures
 * included), a look at a result it held, or Module C's chart — with the
 * planner's reason for it and who served the call.
 */
export interface TrailStep {
  label: string
  purpose?: 'context' | 'insight' | null
  thought?: string | null
  tool?: 'sql' | 'peek' | 'describe' | 'chart' | null
  model?: string | null
  detail?: string | null
  question?: string | null
  hints?: string | null
  sql?: string | null
  rowCount?: number | null
  status: 'done' | 'failed'
  error?: string | null
}

/**
 * A `QueryResult` as the dashboards domain stored it.
 *
 * Saved charts are historical records, so the shape has to survive whether or
 * not anything writes it today. One saved now carries its picture in the
 * inherited `chart`, which is what a dashboard rebuilds from; the fields below
 * stay at their empty defaults so a chart saved before Module C still
 * deserializes. Nothing renders those fields any more.
 */
export interface SavedChartResult extends QueryResult {
  insights: Insight[]
  chartTitle: string
  autoChartType: ChartType
  chartData: ChartData
}

export type MessageRole = 'user' | 'assistant' | 'guard' | 'system'

export interface UserMessage {
  id: string
  role: 'user'
  text: string
  at: string
}

export interface AssistantMessage {
  id: string
  role: 'assistant'
  question: string
  result: QueryResult
  at: string
}

/** Guardrail rejection. */
export interface GuardMessage {
  id: string
  role: 'guard'
  question: string
  at: string
  /**
   * Why it was blocked, from the server's `GuardResult.message`. The reason
   * varies — a write attempt, but also SQL that names tables outside the dataset
   * or bundles more than one statement — so the UI must not assume "write
   * attempt". Optional: persisted guard messages predate this field.
   */
  message?: string
}

/** Inline system notice, e.g. "Source switched to …". */
export interface SystemMessage {
  id: string
  role: 'system'
  text: string
  at: string
}

export type Message = UserMessage | AssistantMessage | GuardMessage | SystemMessage

/**
 * What a conversation runs over: one file, an explicit selection of several, or
 * a whole folder.
 *
 * `datasets` and `folder` are not interchangeable. A selection is frozen when
 * the conversation opens; a folder is re-resolved by the API on every read, so a
 * dataset added to it later joins a conversation that is already open. That is
 * the whole reason a folder has to travel as a folder instead of being expanded
 * to ids by the caller.
 */
export type ConversationSourceType = 'dataset' | 'datasets' | 'folder'

/** The API's descriptor, as returned on `Conversation.source`. */
export interface ConversationSource {
  type: ConversationSourceType
  /** Dataset or folder id; empty for `datasets`, which has no single entity. */
  id: string
  name: string
}

/**
 * The source as the chat screen holds it: the API descriptor plus the resolved
 * datasets, which the canvas needs to render the source pills without a refetch.
 */
export interface ChatSource extends ConversationSource {
  datasets: Dataset[]
}

export interface Conversation {
  id: string
  title: string
  source?: ConversationSource
  datasetIds: string[]
  createdAt: string
  /** Starred by the user from the History screen. */
  favorite?: boolean
  /** Quick or Deep: the default depth of the next questions. */
  mode?: 'quick' | 'deep'
  messages: Message[]
}

export type SuggestionIcon = 'trend' | 'calendar' | 'percent' | 'compare'

export interface Suggestion {
  icon: SuggestionIcon
  text: string
}

/* ---- Dashboards (named boards over one folder) ---- */

/**
 * Where a chart sits, in react-grid-layout's 24-column `lg` space (see
 * `GRID_COLUMNS` in `GridSurface.tsx` and the backend's
 * `app/schemas/dashboards.py` — the only breakpoint that persists).
 *
 * Persisted server-side, not in the browser: a dashboard is something the user
 * *arranged*, not a per-device preference, so it has to look the same on the
 * laptop they built it on and the screen they present it from.
 */
export interface ChartLayout {
  x: number
  y: number
  w: number
  h: number
}

/**
 * One chart on a board: how to rebuild it, never what it showed.
 *
 * `sql` + `spec` and no rows — the numbers come from
 * `getDashboardChartData`, which re-runs the query against the folder's data
 * as it is *now*, every time the board is opened. There is no narrative either:
 * the stored sentence described the numbers as they were, so on a board that
 * recomputes it would be a confident claim about old data under a fresh chart.
 */
export interface DashboardChart {
  id: string
  title: string
  /** The question it came from. Provenance; never re-asked. */
  question: string
  sql: string
  chartType: Exclude<ChartType, 'table'>
  /** Vega-Lite v5, without `data`. */
  spec: Record<string, unknown>
  layout: ChartLayout
}

export interface Dashboard {
  id: string
  name: string
  /** The folder it runs over. Always exactly one, like a conversation. */
  folderId: string
  folderName: string
  chartCount: number
  createdAt: string
  updatedAt: string
}

export interface DashboardDetail extends Dashboard {
  charts: DashboardChart[]
}

/**
 * The result of re-running one chart's query, right now.
 *
 * `status` is explicit because a stale chart is the *expected* failure on a
 * board that recomputes: files in the folder change, and a query naming a
 * column someone renamed stops working. That card says so; the rest carry on.
 */
export interface DashboardChartData {
  chartId: string
  status: 'ok' | 'failed'
  table?: ResultTable
  rowCount: number
  message?: string
}

/* ---- Saved charts (pinned from a conversation; the pool a board draws on) ---- */

export interface SavedChart {
  id: string
  /** Source dataset this chart belongs to (dashboards are per data source). */
  datasetId: string
  datasetName: string
  /** The natural-language question that produced it. */
  question: string
  /** The chart type chosen at save time (may differ from result.autoChartType). */
  chartType: ChartType
  savedAt: string
  result: SavedChartResult
}

/* ---- Saved queries (history / library) ---- */

export interface SavedQuery {
  id: string
  question: string
  datasetId: string
  datasetName: string
  date: string
  chartType: ChartType
}

/* ---- Notifications ---- */

export type NotificationTone = 'ok' | 'warn' | 'err' | 'info'

export interface NotificationAction {
  label: string
  /** Internal route the action navigates to. */
  href: string
}

export interface Notification {
  id: string
  tone: NotificationTone
  title: string
  body: string
  time: string
  read: boolean
  action?: NotificationAction
}

/* ---- History (conversation history, grouped by recency) ---- */

export interface HistoryItem {
  id: string
  title: string
  /** Dataset name or "N datasets". */
  source: string
  questionCount: number
  time: string
  chartType: ChartType
  favorite: boolean
}

export interface HistoryGroup {
  date: string
  items: HistoryItem[]
}
