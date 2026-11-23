import { useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { LegalContext } from './legalContext'
import { LegalModal } from './LegalModal'
import type { LegalDoc } from './legalParts'
import { PRIVACY } from './privacyContent'
import { TERMS } from './termsContent'

/**
 * Owns the Terms / Privacy Policy modal for the whole app (mounted in App.tsx,
 * above the router), so any screen — landing, sign-up, the app, the Help
 * screen, an answer — can open it with `LegalLink` or `useLegal()`.
 */
export function LegalProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<{ doc: LegalDoc; section?: string } | null>(null)

  const openLegal = useCallback((doc: LegalDoc, section?: string) => setOpen({ doc, section }), [])
  const value = useMemo(() => ({ openLegal }), [openLegal])

  return (
    <LegalContext.Provider value={value}>
      {children}
      {open && (
        <LegalModal
          doc={open.doc}
          section={open.section}
          content={open.doc === 'terms' ? TERMS : PRIVACY}
          onSwitch={(doc) => setOpen({ doc })}
          onClose={() => setOpen(null)}
        />
      )}
    </LegalContext.Provider>
  )
}
