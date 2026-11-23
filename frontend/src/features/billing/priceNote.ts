import type { Plan } from '../../types'
import { formatArs, formatIsoDate, formatUsd } from '../../lib/format'

/**
 * The pricing rule, in the words every price list uses (landing, sign-up,
 * account): pesos plus VAT, tied to the official dollar. The figures come from
 * the plan the backend returned — the USD reference values and the rate the
 * peso prices were computed at — never from constants here.
 * (Thesis, Capítulo 5, "El ajuste por tipo de cambio".)
 */
export function priceRuleNote(pro: Plan): string {
  const overage =
    pro.overagePricePerQueryUsd != null ? ` y la consulta adicional, ${formatUsd(pro.overagePricePerQueryUsd)}` : ''
  return (
    'Precios en pesos argentinos, más IVA, con factura electrónica. Están atados al dólar oficial: ' +
    `Pro tiene un valor de referencia de ${formatUsd(pro.priceMonthlyUsd)} por mes ` +
    `(${formatUsd(pro.priceAnnualUsd)} con pago anual)${overage}, ` +
    `calculados con la cotización de ${formatArs(pro.usdArsRate)} del ${formatIsoDate(pro.usdArsRateAsOf)}. ` +
    'El precio en pesos se recalcula cuando cambia la cotización; el plan anual mantiene el precio del día del pago durante los 12 meses.'
  )
}
