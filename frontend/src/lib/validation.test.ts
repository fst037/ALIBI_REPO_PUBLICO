import { describe, expect, it } from 'vitest'
import { passwordMeetsRequirements, passwordStrength, validEmail } from './validation'

// Sign-up refuses a malformed email and a password outside the policy
// (8+ characters, an uppercase letter, a number) before anything is sent.

describe('validEmail', () => {
  it.each(['ana@pyme.com.ar', '  ana@pyme.com  ', 'a.b+c@d.io'])('accepts %s', (email) => {
    expect(validEmail(email)).toBe(true)
  })

  it.each(['', 'ana', 'ana@', 'ana@pyme', '@pyme.com', 'ana pérez@pyme.com'])('refuses %j', (email) => {
    expect(validEmail(email)).toBe(false)
  })
})

describe('password policy', () => {
  it('requires length, an uppercase letter and a number', () => {
    expect(passwordMeetsRequirements('Secreto12')).toBe(true)
    expect(passwordMeetsRequirements('Secr12')).toBe(false)
    expect(passwordMeetsRequirements('secreto12')).toBe(false)
    expect(passwordMeetsRequirements('Secretoss')).toBe(false)
  })

  it('scores a symbol as a bonus on the meter, not as a requirement', () => {
    expect(passwordStrength('')).toBe(0)
    expect(passwordStrength('Secreto12')).toBe(3)
    expect(passwordStrength('Secreto12!')).toBe(4)
    expect(passwordMeetsRequirements('Secreto12')).toBe(true)
  })
})
