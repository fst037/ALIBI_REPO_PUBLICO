import { Check } from '@phosphor-icons/react'
import { Icon } from '../../components/ui'
import { LegalLink } from './LegalLink'
import styles from './TermsConsent.module.css'

export interface TermsConsentProps {
  checked: boolean
  onChange: (checked: boolean) => void
}

/**
 * The express consent no account is created without (Ley 25.326): the Terms,
 * the Privacy Policy, and the international transfer of data. Never pre-ticked.
 * One component because two paths create accounts — the email signup and the
 * first "Continuar con Google" — and the text the user agrees to must be the
 * same on both.
 */
export function TermsConsent({ checked, onChange }: TermsConsentProps) {
  return (
    <label className={styles.consent}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className={styles.consentBox}><Icon icon={Check} size={12} /></span>
      <span>
        Leí y acepto los{' '}
        <LegalLink doc="terms">Términos y condiciones</LegalLink> y la{' '}
        <LegalLink doc="privacy">Política de privacidad</LegalLink>, y presto mi
        consentimiento expreso para que mis datos se traten y se alojen en servidores fuera de la Argentina.
      </span>
    </label>
  )
}
