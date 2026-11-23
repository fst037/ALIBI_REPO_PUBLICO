import type { User } from '../../types'
import * as gen from './generated/auth/auth'
import type { AuthSession, EmailVerificationRequired, GoogleSignInResult, LoginResponse } from './generated/model'
import { authInit, unwrap } from './http'
import { clearSession, setToken } from './session'

export interface RegisterInput {
  email: string
  password: string
  /** First and last name — shown in the onboarding greeting and the account. */
  name: string
  workspace?: string
  /**
   * The version of the Terms and Privacy Policy the user accepted — always
   * `TERMS_VERSION` (lib/legal), the one the pages show. The account records it.
   */
  acceptedTermsVersion: string
}

/*
 * The screens' code step calls `verifyCode(code)` without the email. We thread
 * it here: `register`, and a `login` whose email is still unverified, stash the
 * address the code went to, and `verifyCode` redeems it.
 */
let pendingEmail: string | null = null
/*
 * "Keep me signed in", carried to wherever the session is actually established.
 * When a code is needed the token is issued at verify-email, one call later, so
 * the choice has to survive in between — and it decides two things at once: the
 * server issues a session with no expiry, and the client stores it durably.
 */
let pendingRemember = false

/**
 * Authenticate. Resolves `'authenticated'` when the session is open, or
 * `'verify'` when the account's email is still unverified: a code was emailed
 * and `verifyCode` finishes the sign-in.
 */
export async function login(
  email: string,
  password: string,
  remember = false,
): Promise<'authenticated' | 'verify'> {
  const res = await unwrap<LoginResponse>(gen.login({ email, password, remember }))
  if (res.status === 'authenticated') {
    setToken(res.accessToken, remember)
    pendingEmail = null
    return 'authenticated'
  }
  pendingEmail = res.email
  pendingRemember = remember
  return 'verify'
}

/**
 * Create an account and email its verification code. No session yet: it opens
 * at `verifyCode`, once the address is proven.
 *
 * Any previous session is dropped first. Someone signing up on a browser that
 * still held an earlier session would otherwise keep browsing as that other
 * user — their datasets, their history, their name in the greeting.
 */
export async function register(input: RegisterInput): Promise<void> {
  clearSession()
  const res = await unwrap<EmailVerificationRequired>(gen.register(input))
  pendingEmail = res.email
  // Remembered: the user is about to walk through plan choice and a first
  // upload, and being signed out partway through that is worse than the
  // browser keeping the session.
  pendingRemember = true
}

/**
 * Redeem the 6-digit code and open the session. Throws `ApiError` with the
 * server's message on a wrong (`invalid_code`), expired (`code_expired`) or
 * burnt (`too_many_attempts`) code.
 */
export async function verifyCode(code: string): Promise<void> {
  if (!pendingEmail) throw new Error('No hay un código pendiente de verificación.')
  const session = await unwrap<AuthSession>(
    gen.verifyEmail({ email: pendingEmail, code, remember: pendingRemember }),
  )
  setToken(session.accessToken, pendingRemember)
  pendingEmail = null
}

export interface GoogleSignInOptions {
  /** "Keep me signed in". Defaults to true, as a fresh signup does. */
  remember?: boolean
  /**
   * `TERMS_VERSION` (lib/legal) once the user ticked the consent. Only needed
   * when the Google account has no ALIBI account yet — see `signInWithGoogle`.
   */
  acceptedTermsVersion?: string
}

/**
 * Sign in, or sign up, with the ID token "Continuar con Google" returned. No
 * code step: Google has already verified the address.
 *
 * Resolves `'created'` when this made the account (the screen continues to plan
 * selection, as after `register`) and `'authenticated'` otherwise. Throws
 * `ApiError` with code `terms_required` when the Google account has no ALIBI
 * account yet and no consent came with it: ask for it, then call again with the
 * same credential (it stays valid for an hour).
 */
export async function signInWithGoogle(
  credential: string,
  { remember = true, acceptedTermsVersion }: GoogleSignInOptions = {},
): Promise<'authenticated' | 'created'> {
  const res = await unwrap<GoogleSignInResult>(
    gen.googleSignIn({ credential, remember, acceptedTermsVersion }),
  )
  setToken(res.accessToken, remember)
  pendingEmail = null
  return res.accountCreated ? 'created' : 'authenticated'
}

/** Email a new code to the pending address. Throws `resend_too_soon` within a minute of the last. */
export async function resendCode(): Promise<void> {
  if (!pendingEmail) return
  await unwrap<void>(gen.resendVerificationEmail({ email: pendingEmail }))
}

/** The currently authenticated user (requires a session token). */
export function getCurrentUser(): Promise<User> {
  return unwrap<User>(gen.getCurrentUser(authInit()))
}

/** End the session server-side and drop the local token. */
export async function logout(): Promise<void> {
  try {
    await unwrap<void>(gen.logout(authInit()))
  } finally {
    clearSession()
  }
}
