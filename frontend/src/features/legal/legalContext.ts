import { createContext, useContext } from 'react'
import type { LegalDoc } from './legalParts'

export interface LegalContextValue {
  /** Open a legal document over the current view, optionally at one clause. */
  openLegal: (doc: LegalDoc, section?: string) => void
}

export const LegalContext = createContext<LegalContextValue | null>(null)

/** Access to the legal modal. Needs `LegalProvider` above (App.tsx). */
export function useLegal(): LegalContextValue {
  const ctx = useContext(LegalContext)
  if (!ctx) throw new Error('useLegal must be used inside <LegalProvider>')
  return ctx
}
