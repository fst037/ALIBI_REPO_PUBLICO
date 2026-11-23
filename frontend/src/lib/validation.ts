/* ============================================================
   Shared form validation for the auth flows (login, signup)
   and account settings. Keeps the email regex and the password
   policy in one place instead of duplicated per screen.
   ============================================================ */

/** Lightweight email format check (matches the prototype's original regex). */
export const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

export function validEmail(value: string): boolean {
  return EMAIL_RE.test(value.trim())
}

export interface PasswordRule {
  key: string
  label: string
  test: (password: string) => boolean
}

/**
 * Hard password requirements enforced at registration and password change.
 * Mirrors the copy in Cuenta → Seguridad ("al menos 8 caracteres, con
 * mayúsculas y números") and the contract's `RegisterRequest.password`
 * (`minLength: 8`).
 */
export const PASSWORD_RULES: PasswordRule[] = [
  { key: 'length', label: 'Al menos 8 caracteres', test: (p) => p.length >= 8 },
  { key: 'uppercase', label: 'Una letra mayúscula', test: (p) => /[A-Z]/.test(p) },
  { key: 'number', label: 'Un número', test: (p) => /\d/.test(p) },
]

/** True when every hard requirement in `PASSWORD_RULES` is satisfied. */
export function passwordMeetsRequirements(password: string): boolean {
  return PASSWORD_RULES.every((rule) => rule.test(password))
}

/**
 * Strength score 0–4 for the visual meter: the three hard requirements plus a
 * bonus point for a symbol. This is not the gate — `passwordMeetsRequirements`
 * is; the symbol only nudges the meter toward "strong".
 */
export function passwordStrength(password: string): number {
  let score = 0
  if (password.length >= 8) score++
  if (/[A-Z]/.test(password)) score++
  if (/\d/.test(password)) score++
  if (/[^A-Za-z0-9]/.test(password)) score++
  return score
}
