import { Gauge, ShieldWarning } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import { Icon } from '../../components/ui'
import styles from './conversation.module.css'

/** Shown when no reason came from the server (persisted guard messages predate the field). */
const FALLBACK =
  'Solo ejecuto consultas de lectura, así que nunca modifico ni borro tu información.'

export interface GuardNoticeProps {
  /** The server's `GuardResult.message` — already user-facing copy. */
  message?: string
  /**
   * The plan refused the question (monthly quota or overage cap). Not the
   * guardrail: rephrasing will not help, so the hint points at the account.
   */
  planLimit?: boolean
}

/**
 * Guardrail rejection.
 *
 * The reason is rendered from the server rather than hardcoded, because the
 * guardrail blocks several different things: a write/DDL attempt, SQL naming
 * tables or columns outside the conversation's dataset, more than one statement,
 * or model output that couldn't be parsed. This component used to always explain
 * a write attempt, which was actively misleading for the other cases — a
 * hallucinated table was reported as "I can only read your data".
 *
 * The closing hint is worded to hold for every case; the previous advice
 * ("rephrase it as “show me…”") only made sense for write attempts.
 */
export function GuardNotice({ message, planLimit = false }: GuardNoticeProps) {
  if (planLimit) {
    return (
      <div className={styles.guard}>
        <Icon icon={Gauge} size={22} weight="fill" className={styles.guardIco} />
        <div>
          <div style={{ fontWeight: 600, fontSize: 13.5, color: 'var(--fg-1)' }}>
            Llegaste al límite de tu plan
          </div>
          <p style={{ margin: '6px 0 10px', color: 'var(--fg-2)', fontSize: 13, lineHeight: 1.5 }}>{message}</p>
          <Link className="btn btn-primary btn-sm" to="/cuenta?tab=billing">Ver mi plan</Link>
        </div>
      </div>
    )
  }
  return (
    <div className={styles.guard}>
      <Icon icon={ShieldWarning} size={22} weight="fill" className={styles.guardIco} />
      <div>
        <div style={{ fontWeight: 600, fontSize: 13.5, color: 'var(--fg-1)' }}>
          Consulta bloqueada
        </div>
        <p style={{ margin: '6px 0 0', color: 'var(--fg-2)', fontSize: 13, lineHeight: 1.5 }}>
          {message || FALLBACK}
        </p>
        <p style={{ margin: '6px 0 0', color: 'var(--fg-3)', fontSize: 12.5, lineHeight: 1.5 }}>
          Podés reformular la pregunta o revisar el SQL antes de ejecutarlo.
        </p>
      </div>
    </div>
  )
}
