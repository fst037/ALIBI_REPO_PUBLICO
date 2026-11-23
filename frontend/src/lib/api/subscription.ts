import type { BillingCycle, PlanId, Subscription } from '../../types'
import * as gen from './generated/billing/billing'
import { authInit, unwrap } from './http'

export function getSubscription(): Promise<Subscription> {
  return unwrap<Subscription>(gen.getSubscription(authInit()))
}

/** Where the user was when they left for Mercado Pago, so the result page can send them back. */
const CHECKOUT_FROM_KEY = 'alibi.checkout.from'
export type CheckoutOrigin = 'registro' | 'cuenta'

/**
 * Start paying for a paid plan: returns Mercado Pago's hosted page, which the
 * caller sends the browser to. The price is the backend's, not ours.
 */
export async function startCheckout(
  planId: PlanId,
  cycle: BillingCycle,
  from: CheckoutOrigin,
): Promise<{ checkoutUrl: string; amountArs: number }> {
  const session = await unwrap<{ checkoutUrl: string; amountArs: number }>(
    gen.startCheckout({ planId, cycle, returnOrigin: window.location.origin }, authInit()),
  )
  try {
    sessionStorage.setItem(CHECKOUT_FROM_KEY, from)
  } catch {
    // Only decides where the result page leads; it works without it.
  }
  return session
}

/** Where the user started paying from (read once by the result page). */
export function takeCheckoutOrigin(): CheckoutOrigin {
  try {
    const from = sessionStorage.getItem(CHECKOUT_FROM_KEY)
    sessionStorage.removeItem(CHECKOUT_FROM_KEY)
    return from === 'registro' ? 'registro' : 'cuenta'
  } catch {
    return 'cuenta'
  }
}

/** After Mercado Pago sends the user back: the backend verifies the payment and activates the plan. */
export function confirmPayment(paymentId: string): Promise<Subscription> {
  return unwrap<Subscription>(gen.confirmCheckout({ paymentId }, authInit()))
}

/** Switch the active plan (registration or upgrade). */
export function upgradePlan(planId: PlanId, cycle: BillingCycle): Promise<Subscription> {
  return unwrap<Subscription>(gen.upgradePlan({ planId, cycle }, authInit()))
}

/**
 * Turn pay-per-query overage on or off and set its monthly cap (Pro only —
 * Free answers 402). `capArs` null = no cap.
 */
export function setOverage(enabled: boolean, capArs: number | null): Promise<Subscription> {
  return unwrap<Subscription>(gen.setOverage({ enabled, capArs }, authInit()))
}
