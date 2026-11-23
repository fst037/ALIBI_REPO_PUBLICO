"""`billing` domain routes: subscription, overage, payment method, ledger.

All endpoints require a bearer token and operate on the caller's own billing.
"""

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.api.v1.deps import require_user
from app.schemas.auth import User
from app.core.errors import ApiException
from app.schemas.billing import (
    CheckoutRequest,
    CheckoutSession,
    ConfirmPaymentRequest,
    CostLedger,
    OverageRequest,
    PaymentMethod,
    PaymentMethodRequest,
    Subscription,
    UpgradeRequest,
)
from app.schemas.plans import PlanId
from app.services import billing as store
from app.services import checkout

router = APIRouter(tags=["billing"])


@router.get("/subscription", response_model=Subscription, summary="Active subscription + usage.")
def get_subscription(user: User = Depends(require_user)) -> Subscription:
    return store.get_subscription(user.id)


@router.post(
    "/subscription/upgrade",
    response_model=Subscription,
    summary="Move to the Free plan (cancel). A paid plan is bought through `/billing/checkout`.",
)
def upgrade_plan(request: UpgradeRequest, user: User = Depends(require_user)) -> Subscription:
    if request.planId is not PlanId.free:
        raise ApiException(402, "payment_required", "Para pasar a un plan pago tenés que completar el pago.")
    return store.upgrade(user.id, request)


@router.post(
    "/billing/checkout",
    response_model=CheckoutSession,
    summary="Start paying for a plan: returns the Mercado Pago page to send the user to.",
)
def start_checkout(request: CheckoutRequest, user: User = Depends(require_user)) -> CheckoutSession:
    return checkout.start(user.id, request)


@router.post(
    "/billing/checkout/confirm",
    response_model=Subscription,
    summary="After returning from Mercado Pago: verify the payment and activate the plan.",
)
def confirm_checkout(request: ConfirmPaymentRequest, user: User = Depends(require_user)) -> Subscription:
    """Reads the payment from Mercado Pago (the browser's word is not taken) and
    moves the plan only if it is approved, the user's own, and for the right
    amount. Idempotent."""
    return checkout.confirm(user.id, request.paymentId)


@router.put("/billing/payment-method", response_model=PaymentMethod, summary="Payment method (token).")
def set_payment_method(
    request: PaymentMethodRequest, user: User = Depends(require_user)
) -> PaymentMethod:
    return store.set_payment_method(user.id, request)


@router.put(
    "/billing/overage",
    response_model=Subscription,
    summary="Turn pay-per-query overage on or off, and set its monthly cap.",
)
def set_overage(request: OverageRequest, user: User = Depends(require_user)) -> Subscription:
    """Pro only (402 on Free). Queries beyond the plan's monthly quota are billed
    at the plan's `overagePricePerQuery`, in arrears, up to `capArs` a month."""
    return store.set_overage(user.id, request)


@router.get("/billing/ledger", response_model=CostLedger, summary="Cost ledger by date range.")
def get_cost_ledger(
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    user: User = Depends(require_user),
) -> CostLedger:
    return store.get_ledger(user.id, date_from, date_to)


@router.get("/billing/ledger/export", summary="Export the cost ledger to CSV.")
def export_cost_ledger(
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    user: User = Depends(require_user),
) -> Response:
    ledger = store.get_ledger(user.id, date_from, date_to)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "date", "actionType", "datasetName", "rowsReturned", "costUsd", "costArs"])
    for e in ledger.entries:
        writer.writerow([e.id, e.date, e.actionType, e.datasetName or "", e.rowsReturned, e.costUsd, e.costArs])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="cost-ledger.csv"'},
    )
