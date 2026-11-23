"""Prices in pesos from their USD reference value.

The business model (thesis, Capítulo 5, "El ajuste por tipo de cambio") prices
every plan in dollars because its costs are in dollars, but shows and charges
it in pesos: the peso price is the USD reference value times the official
dollar rate, recalculated whenever the rate changes. Pro is US$ 20 a month, so
at the reference rate of $ 1.550 it is $ 31.000; the overage query is US$ 0,08,
so $ 124.

Plan prices are whole pesos (round half up): that is how they are published,
and a price list of $ 31.000,00 / $ 123,99 helps nobody.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.core.config import get_settings


def usd_ars_rate() -> float:
    return get_settings().usd_ars_rate


def usd_ars_rate_as_of() -> str:
    return get_settings().usd_ars_rate_as_of


def to_ars(usd: float | Decimal) -> float:
    """`usd` in whole pesos at the current official rate."""
    pesos = Decimal(str(usd)) * Decimal(str(usd_ars_rate()))
    return float(pesos.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


__all__ = ["to_ars", "usd_ars_rate", "usd_ars_rate_as_of"]
