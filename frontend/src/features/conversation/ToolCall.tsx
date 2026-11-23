import { ChartBar, Database, Eye, ListMagnifyingGlass } from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { Icon } from '../../components/ui'
import { formatSql } from '../../lib/sqlFormat'
import styles from './conversation.module.css'

type Tool = 'sql' | 'peek' | 'describe' | 'chart'

const TOOLS: Record<Tool, { name: string; icon: PhosphorIcon }> = {
  sql: { name: 'Módulo A · consulta SQL', icon: Database },
  peek: { name: 'Lectura de un resultado', icon: Eye },
  describe: { name: 'Análisis de una columna', icon: ListMagnifyingGlass },
  chart: { name: 'Módulo C · gráfico', icon: ChartBar },
}

/** The parts of a run step or a trail step that say what was called. */
export interface ToolCallStep {
  tool?: Tool | null
  model?: string | null
  question?: string | null
  hints?: string | null
  sql?: string | null
  rowCount?: number | null
  detail?: string | null
  error?: string | null
  status: string
}

/**
 * What a step called and what it got back — the tool, who served it, what it
 * was asked, and the outcome — drawn the same way in the live progress view
 * and in "Cómo llegué a esto", so the two tell one story. `sqlInline` keeps the query to one line while the run is live.
 */
export function ToolCall({ step, sqlInline = false }: { step: ToolCallStep; sqlInline?: boolean }) {
  if (!step.tool) return null
  const tool = TOOLS[step.tool]
  const failed = step.status === 'failed'
  const done = step.status === 'done'
  // The fine-tuned adapter's label is internal detail; only Bedrock ids are shown.
  const showModel = !!step.model && !step.model.startsWith('modelo ajustado de ALIBI')
  return (
    <div className={styles.toolCall}>
      <div className={styles.toolHead}>
        <span className={styles.toolChip}>
          <Icon icon={tool.icon} size="sm" />
          {tool.name}
        </span>
        {showModel && <span className={styles.toolModel}>{step.model}</span>}
      </div>
      {step.sql &&
        (sqlInline ? (
          <code className={styles.toolSqlInline} title={step.sql}>
            {step.sql}
          </code>
        ) : (
          <pre className={styles.trailSql}>{formatSql(step.sql)}</pre>
        ))}
      {done && step.tool === 'sql' && step.rowCount != null && (
        <p className={styles.toolResult}>
          → {step.rowCount} {step.rowCount === 1 ? 'fila' : 'filas'}
        </p>
      )}
      {step.detail && <p className={styles.toolResult}>→ {step.detail}</p>}
      {failed && step.error && <p className={styles.trailError}>{step.error}</p>}
    </div>
  )
}
