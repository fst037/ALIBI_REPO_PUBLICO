import type { BillingCycle, Plan, PlanId } from '../../types'
import * as gen from './generated/plans/plans'
import { ApiError, unwrap } from './http'

/** Plan catalog — public endpoint, no auth needed. */
export function listPlans(): Promise<Plan[]> {
  return unwrap<Plan[]>(gen.listPlans())
}

export async function getPlan(id: string): Promise<Plan | null> {
  try {
    return await unwrap<Plan>(gen.getPlan(id as PlanId))
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

/** Monthly price for the given billing cycle (annual shows the discounted rate). */
export function priceFor(plan: Plan, cycle: BillingCycle): number {
  return cycle === 'annual' ? plan.priceAnnual : plan.priceMonthly
}
