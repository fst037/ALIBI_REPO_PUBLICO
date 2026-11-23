import { useLayoutEffect, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUp, MagnifyingGlassPlus, Lightning, LockSimple, ShieldCheck } from '@phosphor-icons/react'
import { Button, Icon, Segmented } from '../../components/ui'
import type { RunMode } from '../../lib/api'
import styles from './conversation.module.css'

export interface ComposerProps {
  value: string
  onChange: (value: string) => void
  /** `override` asks this one question at the other depth, leaving the default alone. */
  onSend: (override?: RunMode) => void
  /** Quick or Deep: the conversation's default for the next questions. */
  mode: RunMode
  onModeChange: (mode: RunMode) => void
  /**
   * Why Deep is unavailable, if it is:
   * `plan` — Free, whose plan does not include it (Profunda is shown locked
   * with a way to the plan); `quota` — Pro past its monthly quota, where extra
   * queries are answered Quick. Either way there is no one-question override
   * to Deep. The backend refuses it anyway (402); this is what the user is
   * told first.
   */
  deepLock?: 'plan' | 'quota' | null
}

const MODES: { value: RunMode; label: string }[] = [
  { value: 'quick', label: 'Rápida' },
  { value: 'deep', label: 'Profunda' },
]

/**
 * Bottom query composer. Enter sends, Shift+Enter adds a newline.
 *
 * The "in English" cue is functional, not decorative: the Text-to-SQL model was
 * fine-tuned on English questions only and there is no translation step. A question in another language reaches the
 * model untranslated and degrades the generated SQL with no error and no
 * warning, so the placeholder is the only thing standing between the user and a
 * silently bad answer.
 */
export function Composer({ value, onChange, onSend, mode, onModeChange, deepLock = null }: ComposerProps) {
  const deepAllowed = deepLock === null
  // Without Deep the effective depth is always Quick, whatever the conversation
  // saved says — the backend answers it Quick too.
  const current: RunMode = deepAllowed ? mode : 'quick'
  // The one-question override: the other depth, without moving the
  // default. Only offered when the plan includes both.
  const other: RunMode = current === 'quick' ? 'deep' : 'quick'
  const otherLabel = other === 'deep' ? 'Profunda' : 'Rápida'
  const canOverride = deepAllowed
  const empty = value.trim().length === 0

  // Grow with the text (the CSS max-height caps it, then it scrolls) so a long
  // question is never hidden behind a one-line box.
  const areaRef = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const el = areaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])

  const options = MODES.map((m) =>
    m.value === 'deep' && !deepAllowed
      ? deepLock === 'plan'
        ? { ...m, label: 'Profunda · Pro', disabled: true, title: 'El modo Profundo está disponible en el plan Pro.' }
        : {
            ...m,
            disabled: true,
            title: 'Ya usaste las consultas de tu plan este mes: las adicionales se responden en modo Rápido.',
          }
      : m,
  )

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (empty) return
      onSend(canOverride && (e.ctrlKey || e.metaKey) ? other : undefined)
    }
  }
  return (
    <div className={styles.composerWrap}>
      <div className={styles.composerInner}>
        <div className="composer">
          <textarea
            ref={areaRef}
            rows={1}
            placeholder="Preguntá sobre tus datos…"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={onKey}
          />
          {canOverride && (
            <Button
              variant="ghost"
              iconOnly
              onClick={() => onSend(other)}
              disabled={empty}
              aria-label={`Enviar solo esta pregunta en modo ${otherLabel}`}
              title={`Solo esta pregunta en modo ${otherLabel} (Ctrl+Enter)`}
            >
              <Icon icon={other === 'deep' ? MagnifyingGlassPlus : Lightning} size="sm" />
            </Button>
          )}
          <Button variant="primary" iconOnly onClick={() => onSend()} disabled={empty} aria-label="Enviar">
            <Icon icon={ArrowUp} size="sm" />
          </Button>
        </div>
        <div className={styles.composerHint}>
          <span className={styles.composerMode} title="Rápida: pocos pasos, responde enseguida. Profunda (plan Pro): investiga más pasos y redacta con un modelo más capaz.">
            Respuesta
            <Segmented options={options} value={current} onChange={onModeChange} aria-label="Profundidad de la respuesta" />
            {deepLock === 'plan' && (
              <Link to="/cuenta?tab=billing" className={styles.composerUpsell}>
                <Icon icon={LockSimple} size={12} />Profunda con Pro
              </Link>
            )}
            {deepLock === 'quota' && (
              <span className={styles.composerUpsell} style={{ color: 'var(--fg-3)' }}>
                <Icon icon={LockSimple} size={12} />Consultas adicionales: modo Rápido
              </span>
            )}
          </span>
          <span>
            <Icon icon={ShieldCheck} size="sm" style={{ verticalAlign: -2 }} /> Consultas de solo lectura — tus datos nunca se modifican
          </span>
        </div>
      </div>
    </div>
  )
}
