"""In-memory plan catalog: the fallback with no database. Seeded from fixtures.

The fixture holds the USD reference prices; peso prices are computed on every
read, as the database path does (`repositories.plans.priced_plan`).
"""

from app.repositories.plans import priced_plan
from app.schemas.plans import Plan, PlanId
from app.services._fixtures import load_json

_seed: list[dict] = load_json("plans.json")


def _priced(p: dict) -> Plan:
    return priced_plan(
        id=p["id"],
        name=p["name"],
        price_monthly_usd=p["priceMonthlyUsd"],
        price_annual_usd=p["priceAnnualUsd"],
        overage_price_usd=p["overagePricePerQueryUsd"],
        recommended=p["recommended"],
        features=p["features"],
        limits=p["limits"],
    )


def list_plans() -> list[Plan]:
    return [_priced(p) for p in _seed]


def get_plan(plan_id: PlanId) -> Plan | None:
    return next((_priced(p) for p in _seed if p["id"] == plan_id), None)
