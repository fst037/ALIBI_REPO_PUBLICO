"""Pydantic models for the `billing` tag of `contracts/auth.yaml`:
subscription (with its overage), payment method and cost ledger.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.plans import BillingCycle, PlanId


class UsageMetric(BaseModel):
    used: int
    limit: int | None  # null = no cap
    label: str


class PaymentMethod(BaseModel):
    brand: str
    last4: str
    expiry: str


class Invoice(BaseModel):
    id: str
    date: str
    amount: float
    status: Literal["paid", "pending", "failed"]


class SubscriptionUsage(BaseModel):
    queries: UsageMetric
    datasets: UsageMetric
    rowsPerQuery: UsageMetric


class Overage(BaseModel):
    """Pay-per-query beyond the plan's monthly quota (Pro only).

    `queries` and `amountArs` are this calendar month's, derived from the cost
    ledger at read time; nothing about them is stored.
    """

    #: False on a plan that has no overage (Free), whatever was stored.
    available: bool
    enabled: bool
    #: The most the user accepts to be billed for overage in a month, ARS.
    #: null = no cap.
    capArs: float | None
    pricePerQueryArs: float | None
    queries: int
    amountArs: float


class Subscription(BaseModel):
    planId: PlanId
    cycle: BillingCycle
    usage: SubscriptionUsage
    overage: Overage
    paymentMethod: PaymentMethod | None
    invoices: list[Invoice]


class UpgradeRequest(BaseModel):
    planId: PlanId
    cycle: BillingCycle
    paymentToken: str | None = None


class PaymentMethodRequest(BaseModel):
    paymentToken: str


class CheckoutRequest(BaseModel):
    """Start paying for a plan on Mercado Pago's hosted page."""

    planId: PlanId
    cycle: BillingCycle
    #: The browser's own origin (`https://host[:port]`), where Mercado Pago sends
    #: the user back to. Must be one of the API's allowed origins.
    returnOrigin: str


class CheckoutSession(BaseModel):
    checkoutUrl: str
    #: What the user will be charged, ARS, VAT included.
    amountArs: float


class ConfirmPaymentRequest(BaseModel):
    #: The `payment_id` Mercado Pago appended to the return URL. Only a lookup
    #: key: whether it was paid, and for whom, is read from Mercado Pago.
    paymentId: str = Field(pattern=r"^\d{1,20}$")


class OverageRequest(BaseModel):
    enabled: bool
    #: ARS per month; null = no cap. Must be positive when given.
    capArs: float | None = Field(default=None, gt=0)


class CostLedgerEntry(BaseModel):
    id: str
    date: str
    actionType: Literal["query", "visualization"]
    datasetId: str | None = None
    datasetName: str | None = None
    rowsReturned: int
    costUsd: float
    costArs: float


class CostLedger(BaseModel):
    entries: list[CostLedgerEntry]
    totalUsd: float
    totalArs: float
