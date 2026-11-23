import { Check, ListChecks, WarningCircle } from '@phosphor-icons/react'
import { Disclosure, Icon } from '../../components/ui'
import type { TrailStep } from '../../types'
import { cx } from '../../lib/cx'
import { formatSql } from '../../lib/sqlFormat'
import { ToolCall } from './ToolCall'
import styles from './conversation.module.css'

/**
 * "How I got here": every step behind an answer, in order, collapsed —
 * the same steps the progress view showed live, each with the planner's reason
 * and the call it led to (`ToolCall`).
 *
 * Context steps — the ones the assistant ran to understand the data — appear
 * only here, never as a chart in the answer (the multi-chart rule). A
 * failed step and its retry are both listed, with the error: hiding the failure
 * would make the retry look like the first attempt.
 */
export function HowIGotHere({ trail }: { trail: TrailStep[] }) {
  const label = `Cómo llegué a esto · ${trail.length} ${trail.length === 1 ? 'paso' : 'pasos'}`
  return (
    <Disclosure label={label} icon={ListChecks}>
      <ol className={styles.trail}>
        {trail.map((step, index) => {
          const failed = step.status === 'failed'
          return (
            <li key={index} className={cx(styles.trailStep, failed && styles.trailFailed)}>
              <span className={styles.trailIco}>
                <Icon icon={failed ? WarningCircle : Check} size="sm" />
              </span>
              <div className={styles.trailBody}>
                <div className={styles.trailHead}>
                  <span>{step.label}</span>
                  {step.purpose && (
                    <span className={styles.trailTag}>
                      {step.purpose === 'context' ? 'para entender los datos' : 'hallazgo'}
                    </span>
                  )}
                  {!failed && !step.tool && step.rowCount != null && (
                    <span className={styles.trailRows}>
                      {step.rowCount} {step.rowCount === 1 ? 'fila' : 'filas'}
                    </span>
                  )}
                </div>
                {step.thought && <p className={styles.trailThought}>{step.thought}</p>}
                {step.tool ? (
                  <ToolCall step={step} />
                ) : (
                  // Answers stored before steps named their tool (2026-10-16).
                  <>
                    {step.question && <p className={styles.trailQuestion}>{step.question}</p>}
                    {failed && step.error && <p className={styles.trailError}>{step.error}</p>}
                    {step.sql && <pre className={styles.trailSql}>{formatSql(step.sql)}</pre>}
                  </>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </Disclosure>
  )
}
