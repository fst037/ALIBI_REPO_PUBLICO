import type { MouseEvent } from 'react'
import { Code, Copy, Info, Table as TableIcon } from '@phosphor-icons/react'
import { Button, DataTable, Disclosure, Icon, Pill, SqlBlock } from '../../components/ui'
import type { ChartSpec, QueryResult, ResultTable } from '../../types'
import { cx } from '../../lib/cx'
import { formatSql } from '../../lib/sqlFormat'
import { AI_NOTICE } from '../../lib/legal'
import { LegalLink } from '../legal/LegalLink'
import { toColumns } from './tableAdapter'
import { ExportMenu } from '../export/ExportMenu'
import { AnswerChart } from './AnswerChart'
import { HowIGotHere } from './HowIGotHere'
import styles from './conversation.module.css'

export interface AnswerCardProps {
  result: QueryResult
  sourceLabel: string
  /** The question that produced this answer (stored when saving). */
  question: string
  /** Active source — the folder the conversation runs over. */
  datasetId: string
  datasetName: string
  /** Server id of the assistant message. */
  messageId?: string
}

/**
 * A full assistant answer: the explanation, then each finding — its SQL behind
 * a disclosure, Module C's chart, its rows — then "How I got here".
 *
 * **One finding or several**. The agent harness may keep more than one
 * result for the user; then `result.findings` lists them, each with its own
 * sentence, chart and rows under the summary. With one finding (the common case,
 * and every answer stored before the harness) `findings` is empty and the
 * top-level fields are the one result, laid out exactly as before. The steps the
 * harness ran only to understand the data never appear here as charts — they
 * live in "How I got here".
 *
 * **The chart is optional and its absence is not an error.** `chart` is null
 * when Module C judged that no chart improves on the table (a single total, one
 * row) or when nothing it proposed validated. There is deliberately no template
 * standing in — a chart type picked from row shapes is the deterministic
 * classifier that was removed rather than left to disagree with a real model.
 *
 * **The rows are behind a disclosure, open only when there is no chart.** The
 * sentence answers the question and the chart shows the shape; the table is the
 * evidence, wanted when someone doubts one of the first two. When Module C
 * returns no chart, closing the table would leave a sentence and two grey bars,
 * so in that case the rows are the visual and they are shown.
 */
export function AnswerCard({
  result,
  sourceLabel,
  question,
  datasetId,
  messageId,
}: AnswerCardProps) {
  const findings = result.findings ?? []
  const trail = result.trail ?? []

  return (
    <div className={cx('glass', styles.ansCard, 'fade-up')}>
      <div className={styles.ansHead}>
        <span className={styles.miniMark}>A</span>Análisis de {sourceLabel}
        <div style={{ marginLeft: 'auto' }}>
          <ExportMenu result={result} filenameBase={question} />
        </div>
      </div>
      <div className={styles.trace}>
        <Pill tone="accent"><Icon icon={Code} size={12} />Módulo A</Pill>
        {findings.length > 1 ? (
          <Pill>{findings.length} hallazgos</Pill>
        ) : (
          <Pill><Icon icon={TableIcon} size={12} />{result.rowsLabel}</Pill>
        )}
      </div>

      <div className={styles.aiResponse}>
        <div className={styles.aiSummary}><p>{result.naturalResponse}</p></div>

        {findings.length > 1 ? (
          findings.map((finding, index) => (
            <section key={index} className={styles.finding}>
              <p className={styles.findingText}>{finding.text}</p>
              <ResultBlock
                sql={finding.sql}
                chart={finding.chart ?? null}
                table={finding.table}
                rowsLabel={finding.rowsLabel}
                datasetId={datasetId}
                messageId={messageId}
                findingIndex={index}
              />
            </section>
          ))
        ) : (
          <ResultBlock
            sql={result.sql}
            chart={result.chart ?? null}
            table={result.table}
            rowsLabel={result.rowsLabel}
            datasetId={datasetId}
            messageId={messageId}
          />
        )}

        {trail.length > 0 && <HowIGotHere trail={trail} />}

        {/* Required next to every result (Terms, "Alcance de los resultados"). */}
        <p className={styles.aiNotice}>
          <Icon icon={Info} size={13} />
          <span>
            {AI_NOTICE}{' '}
            <LegalLink doc="terms" section="resultados">Más información</LegalLink>
          </span>
        </p>
      </div>
    </div>
  )
}

interface ResultBlockProps {
  sql: string
  chart: ChartSpec | null
  table: ResultTable
  rowsLabel: string
  datasetId: string
  messageId?: string
  findingIndex?: number
}

/** One result's evidence: the SQL, the chart (if any), the rows. */
function ResultBlock({ sql, chart, table, rowsLabel, datasetId, messageId, findingIndex }: ResultBlockProps) {
  const columns = toColumns(table)

  // Copies what is on screen, not the one-liner behind it: the pretty version
  // is still the same valid query, and copying something that doesn't match
  // what you just read is its own small surprise.
  const copySql = (e: MouseEvent) => {
    e.stopPropagation()
    void navigator.clipboard?.writeText(formatSql(sql))
  }

  return (
    <>
      <SqlBlock
        sql={sql}
        actions={
          <Button variant="ghost" size="sm" onClick={copySql}>
            <Icon icon={Copy} size="sm" />Copiar
          </Button>
        }
      />

      {chart && (
        <AnswerChart
          chart={chart}
          table={table}
          datasetId={datasetId}
          messageId={messageId}
          findingIndex={findingIndex}
        />
      )}

      <Disclosure label={`Datos · ${rowsLabel}`} icon={TableIcon} defaultOpen={!chart}>
        <DataTable columns={columns} rows={table.rows} />
      </Disclosure>
    </>
  )
}
