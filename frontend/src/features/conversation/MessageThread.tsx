import { Swap } from '@phosphor-icons/react'
import { Icon } from '../../components/ui'
import { cx } from '../../lib/cx'
import type { ThreadItem } from './useConversation'
import { PipelineSteps } from './PipelineSteps'
import { GuardNotice } from './GuardNotice'
import { AnswerCard } from './AnswerCard'
import styles from './conversation.module.css'

export interface MessageThreadProps {
  messages: ThreadItem[]
  sourceLabel: string
  datasetId: string
  datasetName: string
}

/** Renders the conversation thread: each turn = question + pipeline/guard/answer. */
export function MessageThread({ messages, sourceLabel, datasetId, datasetName }: MessageThreadProps) {
  return (
    <>
      {messages.map((item) => {
        if (item.kind === 'system') {
          return (
            <div key={item.id} className={cx(styles.sysDiv, 'fade-up')}>
              <Icon icon={Swap} size="sm" />
              {item.text}
            </div>
          )
        }
        return (
          <div key={item.id} className={cx(styles.msgRow, 'fade-up')}>
            <div className={styles.msgUser}>
              {item.question}
              {item.mode === 'deep' && <span className={styles.modeTag}>Profunda</span>}
            </div>
            {item.phase === 'piping' && <PipelineSteps steps={item.steps} />}
            {item.phase === 'guard' && <GuardNotice message={item.guardMessage} planLimit={item.planLimit} />}
            {item.phase === 'answer' && item.result && (
              <AnswerCard
                result={item.result}
                sourceLabel={sourceLabel}
                question={item.question}
                datasetId={datasetId}
                datasetName={datasetName}
                messageId={item.messageId}
              />
            )}
          </div>
        )
      })}
    </>
  )
}
