"""Pydantic models for the `plans` tag of `contracts/auth.yaml`.

Two plans and a pay-per-use tier on top of Pro (the business
model). There is no Business plan: a Pro user who needs more than
250 queries in a month pays each extra query instead of moving to a bigger
fixed plan.

**Prices are pesos tied to the dollar**: each
price has a USD reference value (`*Usd`) and the peso price is that value at
the official rate (`usdArsRate`), recomputed on every read. VAT excluded.
"""

from enum import Enum

from pydantic import BaseModel

from app.schemas.chat import RunMode


class PlanId(str, Enum):
    free = "free"
    pro = "pro"


class BillingCycle(str, Enum):
    monthly = "monthly"
    annual = "annual"


class ExportFormat(str, Enum):
    csv = "csv"
    png = "png"
    pdf = "pdf"


class PlanLimits(BaseModel):
    # null = no practical limit.
    datasets: int | None
    queriesPerMonth: int | None
    rowsPerQuery: int | None
    #: Largest file a single upload may be, in MB.
    maxFileMb: int
    #: How far back the History screen reaches. null = unlimited.
    historyDays: int | None
    exportFormats: list[ExportFormat]
    #: Answer depths the plan may ask for: Free
    #: answers Quick only; Deep — more steps, the more capable synthesis model —
    #: is a Pro feature.
    answerModes: list[RunMode] = [RunMode.quick]


class Plan(BaseModel):
    id: PlanId
    name: str
    #: ISO 4217 of the prices the user sees and pays — ARS, VAT excluded.
    currency: str = "ARS"
    #: Per month, billed monthly, in pesos at `usdArsRate`.
    priceMonthly: float
    #: Monthly rate when billed annually in advance (the discounted one), pesos.
    priceAnnual: float
    #: Price of one query beyond `queriesPerMonth`, billed in arrears, pesos.
    #: null = the plan has no overage: reaching the quota stops the queries.
    overagePricePerQuery: float | None = None
    #: The USD reference values the three prices above are computed from.
    priceMonthlyUsd: float
    priceAnnualUsd: float
    overagePricePerQueryUsd: float | None = None
    #: Official ARS per USD the peso prices were computed at, and its date.
    usdArsRate: float
    usdArsRateAsOf: str
    recommended: bool
    features: list[str]
    limits: PlanLimits
