"""Postgres repository for the `billing` domain (subscription + ledger).

Owner-scoped. Subscription USAGE is derived at read time (not stored): queries
this month and the largest query come from `cost_ledger`, the dataset count from
`datasets`, each capped against the plan's limits. A user with no subscription
row is treated as Free.

The ledger is written once per answered question (`record_query`), with the cost
its model calls reported — see `app/services/billing.py`.
"""

import secrets
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, select

from app.db.models import CostLedgerEntryModel, DatasetModel, InvoiceModel, Subscription as SubscriptionRow
from app.repositories import plans as plans_repo
from app.schemas.billing import (
    CostLedger,
    CostLedgerEntry,
    Invoice,
    Overage,
    OverageRequest,
    PaymentMethod,
    Subscription,
    SubscriptionUsage,
    UpgradeRequest,
    UsageMetric,
)
from app.schemas.plans import BillingCycle, Plan, PlanId


def plan_for(session: Session, user_id: str) -> Plan:
    sub = session.get(SubscriptionRow, user_id)
    plan = plans_repo.get_plan(session, sub.plan_id if sub else PlanId.free)
    assert plan is not None  # seeded by migrations; a missing row is a broken deploy
    return plan


def queries_this_month(session: Session, user_id: str) -> int:
    return session.exec(
        select(func.count())
        .select_from(CostLedgerEntryModel)
        .where(
            CostLedgerEntryModel.user_id == user_id,
            CostLedgerEntryModel.action_type == "query",
            CostLedgerEntryModel.occurred_at >= func.date_trunc("month", func.now()),
        )
    ).one()


def dataset_count(session: Session, user_id: str) -> int:
    return session.exec(
        select(func.count()).select_from(DatasetModel).where(DatasetModel.owner_id == user_id)
    ).one()


def get_subscription(session: Session, user_id: str) -> Subscription:
    sub = session.get(SubscriptionRow, user_id)
    plan = plan_for(session, user_id)
    limits = plan.limits

    payment = None
    if sub and sub.payment_brand is not None:
        payment = PaymentMethod(brand=sub.payment_brand, last4=sub.payment_last4, expiry=sub.payment_expiry)

    queries_used = queries_this_month(session, user_id)
    rows_used = session.exec(
        select(func.coalesce(func.max(CostLedgerEntryModel.rows_returned), 0)).where(
            CostLedgerEntryModel.user_id == user_id,
            CostLedgerEntryModel.occurred_at >= func.date_trunc("month", func.now()),
        )
    ).one()

    invoice_rows = session.exec(
        select(InvoiceModel).where(InvoiceModel.user_id == user_id).order_by(InvoiceModel.issued_on.desc())
    ).all()
    invoices = [
        Invoice(id=row.id, date=row.issued_on.isoformat(), amount=float(row.amount), status=row.status)
        for row in invoice_rows
    ]

    usage = SubscriptionUsage(
        queries=UsageMetric(used=queries_used, limit=limits.queriesPerMonth, label="Consultas este mes"),
        datasets=UsageMetric(used=dataset_count(session, user_id), limit=limits.datasets, label="Datasets"),
        rowsPerQuery=UsageMetric(used=rows_used, limit=limits.rowsPerQuery, label="Filas por consulta"),
    )
    return Subscription(
        planId=plan.id,
        cycle=sub.cycle if sub else BillingCycle.monthly,
        usage=usage,
        overage=overage_of(
            plan,
            enabled=bool(sub and sub.overage_enabled),
            cap_ars=float(sub.overage_cap_ars) if sub and sub.overage_cap_ars is not None else None,
            queries_used=queries_used,
        ),
        paymentMethod=payment,
        invoices=invoices,
    )


def overage_of(plan: Plan, *, enabled: bool, cap_ars: float | None, queries_used: int) -> Overage:
    """The month's overage, from the plan and the queries counted so far.

    Shared with the in-memory store so both backends bill the same way.
    """
    price = plan.overagePricePerQuery
    quota = plan.limits.queriesPerMonth
    if price is None or quota is None:
        return Overage(available=False, enabled=False, capArs=None, pricePerQueryArs=None, queries=0, amountArs=0.0)
    extra = max(0, queries_used - quota)
    return Overage(
        available=True,
        enabled=enabled,
        capArs=cap_ars,
        pricePerQueryArs=price,
        queries=extra,
        amountArs=round(extra * price, 2),
    )


def upgrade(session: Session, user_id: str, request: UpgradeRequest) -> None:
    stmt = pg_insert(SubscriptionRow).values(
        user_id=user_id, plan_id=request.planId.value, cycle=request.cycle.value
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SubscriptionRow.user_id],
        set_={"plan_id": stmt.excluded.plan_id, "cycle": stmt.excluded.cycle},
    )
    session.execute(stmt)


def set_overage(session: Session, user_id: str, request: OverageRequest) -> None:
    cap = Decimal(str(request.capArs)) if request.capArs is not None else None
    stmt = pg_insert(SubscriptionRow).values(
        user_id=user_id, plan_id=PlanId.free.value, overage_enabled=request.enabled, overage_cap_ars=cap
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SubscriptionRow.user_id],
        set_={"overage_enabled": stmt.excluded.overage_enabled, "overage_cap_ars": stmt.excluded.overage_cap_ars},
    )
    session.execute(stmt)


def record_payment(
    session: Session, user_id: str, *, payment_id: str, amount: float, method: PaymentMethod | None
) -> None:
    """The invoice for one approved Mercado Pago payment, and the card it was paid with.

    Keyed by the payment id, so confirming the same payment twice (a reloaded
    return page) writes one invoice.
    """
    invoice_id = f"mp_{payment_id}"
    if session.get(InvoiceModel, invoice_id) is not None:
        return
    session.add(
        InvoiceModel(
            id=invoice_id, user_id=user_id, issued_on=date.today(), amount=Decimal(str(amount)), status="paid"
        )
    )
    row = session.get(SubscriptionRow, user_id)
    if row is not None and method is not None:
        row.payment_brand, row.payment_last4, row.payment_expiry = method.brand, method.last4, method.expiry
        session.add(row)


def has_payment(session: Session, payment_id: str) -> bool:
    return session.get(InvoiceModel, f"mp_{payment_id}") is not None


def set_payment_method(session: Session, user_id: str) -> PaymentMethod:
    # Canned tokenised card details (a real processor returns these).
    method = PaymentMethod(brand="Visa", last4="4242", expiry="08/28")
    stmt = pg_insert(SubscriptionRow).values(
        user_id=user_id,
        plan_id=PlanId.free.value,
        payment_brand=method.brand,
        payment_last4=method.last4,
        payment_expiry=method.expiry,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SubscriptionRow.user_id],
        set_={
            "payment_brand": stmt.excluded.payment_brand,
            "payment_last4": stmt.excluded.payment_last4,
            "payment_expiry": stmt.excluded.payment_expiry,
        },
    )
    session.execute(stmt)
    return method


def record_query(
    session: Session,
    user_id: str,
    *,
    rows_returned: int,
    cost_usd: float,
    cost_ars: float,
    source_name: str | None,
) -> None:
    session.add(
        CostLedgerEntryModel(
            id="cl_" + secrets.token_urlsafe(10),
            user_id=user_id,
            occurred_at=datetime.now(timezone.utc),
            action_type="query",
            dataset_name=source_name,
            rows_returned=rows_returned,
            cost_usd=Decimal(f"{cost_usd:.4f}"),  # numeric(12,4)
            cost_ars=Decimal(f"{cost_ars:.2f}"),  # numeric(14,2)
        )
    )


def get_ledger(session: Session, user_id: str, date_from: str | None, date_to: str | None) -> CostLedger:
    query = select(CostLedgerEntryModel).where(CostLedgerEntryModel.user_id == user_id)
    if date_from:
        query = query.where(CostLedgerEntryModel.occurred_at >= date_from)
    if date_to:
        query = query.where(CostLedgerEntryModel.occurred_at <= date_to)
    query = query.order_by(CostLedgerEntryModel.occurred_at.desc())

    entries = [
        CostLedgerEntry(
            id=row.id,
            date=row.occurred_at.isoformat(),
            actionType=row.action_type,
            datasetId=row.dataset_id,
            datasetName=row.dataset_name,
            rowsReturned=row.rows_returned,
            costUsd=float(row.cost_usd),
            costArs=float(row.cost_ars),
        )
        for row in session.exec(query).all()
    ]
    return CostLedger(
        entries=entries,
        totalUsd=round(sum(e.costUsd for e in entries), 4),
        totalArs=round(sum(e.costArs for e in entries), 2),
    )
