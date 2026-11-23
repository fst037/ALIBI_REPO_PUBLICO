import { useEffect, useState } from 'react'
import * as api from '../../lib/api'
import type { Plan, Subscription } from '../../types'

/**
 * The caller's subscription and the plan it is on — for screens that show a
 * plan limit (file size, dataset count) next to the action it limits.
 *
 * Both null until loaded; a screen renders its limit copy only once it has
 * one, rather than guessing a plan. The backend enforces every limit anyway
 * (app/services/billing.py); this is only what the user is told beforehand.
 */
export function usePlan(): { sub: Subscription | null; plan: Plan | null } {
  const [sub, setSub] = useState<Subscription | null>(null)
  const [plan, setPlan] = useState<Plan | null>(null)

  useEffect(() => {
    let alive = true
    void Promise.all([api.getSubscription(), api.listPlans()])
      .then(([s, plans]) => {
        if (!alive) return
        setSub(s)
        setPlan(plans.find((p) => p.id === s.planId) ?? null)
      })
      .catch(() => {
        // Limit copy is informative only; without it the screen still works.
      })
    return () => {
      alive = false
    }
  }, [])

  return { sub, plan }
}
