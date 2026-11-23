import { Sparkle } from '@phosphor-icons/react'
import { Icon } from '../../components/ui'
import { cx } from '../../lib/cx'
import styles from './conversation.module.css'

export interface GreetingProps {
  sourceLabel: string
}

/** Empty-thread greeting. */
export function Greeting({ sourceLabel }: GreetingProps) {
  return (
    <div className={cx(styles.greet, 'fade-up')}>
      <div className={styles.greetIco}><Icon icon={Sparkle} size={26} /></div>
      <h2>Listo para responder preguntas sobre {sourceLabel}</h2>
      <p>Preguntá lo que quieras.</p>
    </div>
  )
}
