"""Postgres repository for the `plans` domain (public plan catalog)."""

from decimal import Decimal

from sqlmodel import Session, select

from app.core.pricing import to_ars, usd_ars_rate, usd_ars_rate_as_of
from app.db.models import Plan as PlanRow
from app.schemas.plans import Plan, PlanId, PlanLimits


def priced_plan(
    *,
    id: PlanId | str,
    name: str,
    price_monthly_usd: float | Decimal,
    price_annual_usd: float | Decimal,
    overage_price_usd: float | Decimal | None,
    recommended: bool,
    features: list[str],
    limits: dict,
) -> Plan:
    """A plan with its peso prices computed from the USD reference values.

    Computed on every read, never stored: the peso price follows the official
    rate (`app/core/pricing.py`). Shared with the in-memory catalogue so both
    backends price the same way.
    """
    return Plan(
        id=id,
        name=name,
        currency="ARS",
        priceMonthly=to_ars(price_monthly_usd),
        priceAnnual=to_ars(price_annual_usd),
        overagePricePerQuery=None if overage_price_usd is None else to_ars(overage_price_usd),
        priceMonthlyUsd=float(price_monthly_usd),
        priceAnnualUsd=float(price_annual_usd),
        overagePricePerQueryUsd=None if overage_price_usd is None else float(overage_price_usd),
        usdArsRate=usd_ars_rate(),
        usdArsRateAsOf=usd_ars_rate_as_of(),
        recommended=recommended,
        features=features,
        limits=PlanLimits(**limits),
    )


def _plan(row: PlanRow) -> Plan:
    return priced_plan(
        id=row.id,
        name=row.name,
        price_monthly_usd=row.price_monthly_usd,
        price_annual_usd=row.price_annual_usd,
        overage_price_usd=row.overage_price_usd,
        recommended=row.recommended,
        features=row.features,
        limits=row.limits,
    )


def list_plans(session: Session) -> list[Plan]:
    rows = session.exec(select(PlanRow).order_by(PlanRow.price_monthly_usd)).all()
    return [_plan(row) for row in rows]


def get_plan(session: Session, plan_id: PlanId) -> Plan | None:
    row = session.get(PlanRow, plan_id)
    return _plan(row) if row else None
