"""In-memory subscription/billing store: the fallback with no database.

Single-user, so `user_id` is accepted but ignored. Seeded from the subscription
fixture; usage is derived at read time exactly as the database path does it —
queries this month from the ledger, datasets from the datasets store. The ledger
starts with two canned past entries and grows with every answered question.
Mutations persist within a process run; `reset()` restores the seed (tests).

The fixture account is on **Pro**: its seed already holds five datasets, more
than Free's three, so a Free seed would start out over its own limit.
"""

import secrets
from datetime import datetime, timezone

from app.repositories.billing import overage_of
from app.schemas.billing import (
    CostLedger,
    CostLedgerEntry,
    Invoice,
    OverageRequest,
    PaymentMethod,
    PaymentMethodRequest,
    Subscription,
    SubscriptionUsage,
    UpgradeRequest,
    UsageMetric,
)
from app.schemas.plans import BillingCycle, Plan, PlanId
from app.services import datasets_memory, plans_memory
from app.services._fixtures import load_json

_CANNED_LEDGER = [
    CostLedgerEntry(
        id="cl_1",
        date="2026-07-08T10:12:03Z",
        actionType="query",
        datasetId="sales",
        datasetName="sales_2024.csv",
        rowsReturned=5,
        costUsd=0.012,
        costArs=15.6,
    ),
    CostLedgerEntry(
        id="cl_2",
        date="2026-07-07T16:40:04Z",
        actionType="visualization",
        datasetId="sales",
        datasetName="sales_2024.csv",
        rowsReturned=6,
        costUsd=0.008,
        costArs=10.4,
    ),
]


class _State:
    def __init__(self) -> None:
        seed = load_json("subscription.json")
        self.plan_id = PlanId(seed["planId"])
        self.cycle = BillingCycle(seed["cycle"])
        self.payment: PaymentMethod | None = (
            PaymentMethod(**seed["paymentMethod"]) if seed.get("paymentMethod") else None
        )
        self.overage_enabled: bool = seed.get("overageEnabled", False)
        self.overage_cap_ars: float | None = seed.get("overageCapArs")
        self.ledger: list[CostLedgerEntry] = list(_CANNED_LEDGER)
        self.invoices: list[Invoice] = []


_state = _State()


def reset() -> None:
    global _state
    _state = _State()


def plan_for(_user_id: str) -> Plan:
    plan = plans_memory.get_plan(_state.plan_id)
    assert plan is not None
    return plan


def _this_month(entry: CostLedgerEntry) -> bool:
    now = datetime.now(timezone.utc)
    when = datetime.fromisoformat(entry.date.replace("Z", "+00:00"))
    return (when.year, when.month) == (now.year, now.month)


def queries_this_month(_user_id: str) -> int:
    return sum(1 for e in _state.ledger if e.actionType == "query" and _this_month(e))


def dataset_count(user_id: str) -> int:
    return len(datasets_memory.list_datasets(user_id))


def get_subscription(user_id: str) -> Subscription:
    plan = plan_for(user_id)
    used = queries_this_month(user_id)
    rows = max((e.rowsReturned for e in _state.ledger if _this_month(e)), default=0)
    return Subscription(
        planId=plan.id,
        cycle=_state.cycle,
        usage=SubscriptionUsage(
            queries=UsageMetric(used=used, limit=plan.limits.queriesPerMonth, label="Consultas este mes"),
            datasets=UsageMetric(used=dataset_count(user_id), limit=plan.limits.datasets, label="Datasets"),
            rowsPerQuery=UsageMetric(used=rows, limit=plan.limits.rowsPerQuery, label="Filas por consulta"),
        ),
        overage=overage_of(
            plan, enabled=_state.overage_enabled, cap_ars=_state.overage_cap_ars, queries_used=used
        ),
        paymentMethod=_state.payment,
        invoices=list(_state.invoices),
    )


def upgrade(user_id: str, request: UpgradeRequest) -> Subscription:
    _state.plan_id = request.planId
    _state.cycle = request.cycle
    return get_subscription(user_id)


def set_overage(user_id: str, request: OverageRequest) -> Subscription:
    _state.overage_enabled = request.enabled
    _state.overage_cap_ars = request.capArs
    return get_subscription(user_id)


def record_payment(
    _user_id: str, *, payment_id: str, amount: float, method: PaymentMethod | None
) -> None:
    invoice_id = f"mp_{payment_id}"
    if any(i.id == invoice_id for i in _state.invoices):
        return
    _state.invoices.insert(
        0, Invoice(id=invoice_id, date=datetime.now(timezone.utc).date().isoformat(), amount=amount, status="paid")
    )
    if method is not None:
        _state.payment = method


def has_payment(payment_id: str) -> bool:
    return any(i.id == f"mp_{payment_id}" for i in _state.invoices)


def set_payment_method(_user_id: str, _request: PaymentMethodRequest) -> PaymentMethod:
    method = PaymentMethod(brand="Visa", last4="4242", expiry="08/28")
    _state.payment = method
    return method


def record_query(
    _user_id: str, *, rows_returned: int, cost_usd: float, cost_ars: float, source_name: str | None
) -> None:
    _state.ledger.insert(
        0,
        CostLedgerEntry(
            id="cl_" + secrets.token_urlsafe(10),
            date=datetime.now(timezone.utc).isoformat(),
            actionType="query",
            datasetName=source_name,
            rowsReturned=rows_returned,
            costUsd=round(cost_usd, 4),
            costArs=round(cost_ars, 2),
        ),
    )


def get_ledger(_user_id: str, _date_from: str | None, _date_to: str | None) -> CostLedger:
    entries = list(_state.ledger)
    return CostLedger(
        entries=entries,
        totalUsd=round(sum(e.costUsd for e in entries), 4),
        totalArs=round(sum(e.costArs for e in entries), 2),
    )
