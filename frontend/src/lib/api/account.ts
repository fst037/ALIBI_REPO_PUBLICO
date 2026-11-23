import type { User } from '../../types'
import * as gen from './generated/account/account'
import { authInit, unwrap } from './http'

export function getAccount(): Promise<User> {
  return unwrap<User>(gen.getAccount(authInit()))
}

/** Save profile/workspace changes (Cuenta 4.2, per-section save). */
export function updateProfile(
  patch: Partial<Pick<User, 'name' | 'email' | 'workspace'>>,
): Promise<User> {
  return unwrap<User>(gen.updateProfile(patch, authInit()))
}
