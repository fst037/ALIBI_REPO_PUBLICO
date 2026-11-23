"""`billing` domain service — swap point between storage backends, and the one
place the plan's limits are enforced.

Subscription, payment method, overage and cost ledger run against the database
(owner-scoped) when configured, else the in-memory fixtures.

**The business model in code**:

* Free — 20 queries a month, 3 datasets, 5 MB files, 1.000 rows per query,
  7 days of History, CSV export. Reaching a limit stops the action with a 402
  that says what Pro would give.
* Pro — 250 queries, unlimited datasets, 100 MB files, 5.000 rows, full
  History, CSV/PNG/PDF. Past 250 queries, the user may keep going at the
  plan's per-query price (*overage*), billed in arrears, never beyond the
  monthly cap they set. Off by default: nobody is billed for overage they did
  not turn on.
* A "query" is an answered question: one ledger row, written when the answer is
  stored. A guard refusal or a failed run costs the user nothing.
* Notifications at 80 % of the quota and when it, or the overage cap, is
  reached — each once per month.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.core.errors import ApiException
from app.core.usage_meter import ModelCall, cost_usd
from app.db import pool
from app.db.engine import session_scope
from app.repositories import billing as repo
from app.schemas.billing import (
    CostLedger,
    OverageRequest,
    PaymentMethod,
    PaymentMethodRequest,
    Subscription,
    UpgradeRequest,
)
from app.schemas.chat import RunMode
from app.schemas.notifications import NotificationAction
from app.schemas.plans import ExportFormat, Plan, PlanId
from app.services import billing_memory as mem
from app.services import notifications, plans

logger = logging.getLogger("alibi.billing")

_TO_ACCOUNT = NotificationAction(label="Ver mi plan", href="/cuenta?tab=billing")

#: Share of the monthly quota at which the user is warned.
WARN_AT = 0.8


def _ars(amount: float) -> str:
    """`39900` → `$ 39.900` (es-AR grouping, no decimals unless needed)."""
    whole = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return "$ " + whole.replace(",", "X").replace(".", ",").replace("X", ".")


# ---- reads / settings -------------------------------------------------------


def get_subscription(user_id: str) -> Subscription:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_subscription(session, user_id)
    return mem.get_subscription(user_id)


def _pro_quota() -> str:
    """Pro's monthly queries as the Free-plan messages quote them (`250`, `1.000`).

    Read from the catalog so a change of quota is one change, in the plan, and
    not also in every message that points a Free user at Pro."""
    pro = plans.get_plan(PlanId.pro)
    quota = pro.limits.queriesPerMonth if pro else None
    return f"{quota:,}".replace(",", ".") if quota else "más"


def plan_for(user_id: str) -> Plan:
    if pool.is_configured():
        with session_scope() as session:
            return repo.plan_for(session, user_id)
    return mem.plan_for(user_id)


def upgrade(user_id: str, request: UpgradeRequest) -> Subscription:
    if pool.is_configured():
        with session_scope() as session:
            repo.upgrade(session, user_id, request)
            session.flush()
            return repo.get_subscription(session, user_id)
    return mem.upgrade(user_id, request)


def set_overage(user_id: str, request: OverageRequest) -> Subscription:
    if plan_for(user_id).overagePricePerQuery is None:
        raise ApiException(
            402,
            "plan_upgrade_required",
            "Las consultas adicionales están disponibles en el plan Pro.",
        )
    if pool.is_configured():
        with session_scope() as session:
            repo.set_overage(session, user_id, request)
            session.flush()
            return repo.get_subscription(session, user_id)
    return mem.set_overage(user_id, request)


def set_payment_method(user_id: str, request: PaymentMethodRequest) -> PaymentMethod:
    if pool.is_configured():
        with session_scope() as session:
            return repo.set_payment_method(session, user_id)
    return mem.set_payment_method(user_id, request)


def get_ledger(user_id: str, date_from: str | None, date_to: str | None) -> CostLedger:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_ledger(session, user_id, date_from, date_to)
    return mem.get_ledger(user_id, date_from, date_to)


# ---- enforcement --------------------------------------------------------------


def require_query_allowance(user_id: str) -> None:
    """Raise a 402 when the user may not ask another question this month.

    Checked before any model call is spent, both when a run is started and
    again inside it (`conversations.prepare`), so a run queued just before the
    quota ran out is still stopped.
    """
    sub = get_subscription(user_id)
    used, quota = sub.usage.queries.used, sub.usage.queries.limit
    if quota is None or used < quota:
        return

    overage = sub.overage
    if not overage.available:
        raise ApiException(
            402,
            "plan_limit_reached",
            f"Usaste tus {quota} consultas de este mes. Pasá a Pro para tener {_pro_quota()} consultas mensuales.",
        )
    if not overage.enabled:
        raise ApiException(
            402,
            "plan_limit_reached",
            f"Usaste las {quota:,} consultas de tu plan este mes. ".replace(",", ".")
            + "Activá las consultas adicionales en Cuenta para seguir preguntando.",
        )
    price = overage.pricePerQueryArs or 0.0
    if overage.capArs is not None and overage.amountArs + price > overage.capArs:
        raise ApiException(
            402,
            "overage_cap_reached",
            f"Llegaste al tope de {_ars(overage.capArs)} que fijaste para consultas adicionales este mes. "
            "Podés subirlo en Cuenta.",
        )


def row_limit_for(user_id: str) -> int:
    """Rows a query may return: the plan's, never above the platform ceiling.

    The ceiling (`query_row_limit`) is what an answer can carry back through the
    API — a run's rows travel in its JSON — so it bounds every plan.
    """
    ceiling = get_settings().query_row_limit
    plan_rows = plan_for(user_id).limits.rowsPerQuery
    return ceiling if plan_rows is None else min(plan_rows, ceiling)


def require_upload_allowance(user_id: str, size_bytes: int) -> None:
    """Refuse an upload the plan does not allow, before a URL is handed out."""
    plan = plan_for(user_id)
    limits = plan.limits
    max_mb = min(limits.maxFileMb, get_settings().max_upload_mb)
    if size_bytes > max_mb * 1024 * 1024:
        hint = " Con Pro podés subir archivos de hasta 100 MB." if plan.id is PlanId.free else ""
        raise ApiException(
            413, "file_too_large", f"El archivo supera el límite de {max_mb} MB de tu plan.{hint}"
        )
    if limits.datasets is not None:
        count = (
            _with_session(repo.dataset_count, user_id) if pool.is_configured() else mem.dataset_count(user_id)
        )
        if count >= limits.datasets:
            raise ApiException(
                402,
                "plan_limit_reached",
                f"Tu plan admite hasta {limits.datasets} datasets. "
                "Eliminá uno o pasá a Pro para tener datasets ilimitados.",
            )


def require_export_format(user_id: str, fmt: ExportFormat) -> None:
    if fmt not in plan_for(user_id).limits.exportFormats:
        raise ApiException(402, "plan_upgrade_required", "Exportar en este formato requiere el plan Pro.")


def allows_mode(user_id: str, mode: RunMode) -> bool:
    """Whether the user's plan may answer at `mode` (Free: Quick only)."""
    return mode in plan_for(user_id).limits.answerModes


def require_answer_mode(user_id: str, mode: RunMode) -> None:
    """Refuse a depth the plan does not include — asked explicitly, for a
    question or as a conversation's default."""
    if not allows_mode(user_id, mode):
        raise ApiException(
            402,
            "plan_upgrade_required",
            "Las respuestas en modo Profundo están disponibles en el plan Pro. "
            "Con tu plan, ALIBI responde en modo Rápido.",
        )


def in_overage(user_id: str) -> bool:
    """Whether the next question is beyond the plan's monthly quota.

    Extra queries are answered Quick: they are
    priced on a Quick answer's cost, and a Deep one would cost more than the
    extra query earns. Deep comes back with next month's quota.
    """
    queries = get_subscription(user_id).usage.queries
    return queries.limit is not None and queries.used >= queries.limit


def require_deep_within_quota(user_id: str) -> None:
    if in_overage(user_id):
        raise ApiException(
            402,
            "deep_outside_quota",
            "Ya usaste las consultas de tu plan este mes: las consultas adicionales se responden en modo Rápido. "
            "El modo Profundo vuelve con el cupo del mes próximo.",
        )


def history_since(user_id: str) -> datetime | None:
    """The oldest conversation the History screen shows, or None for all."""
    days = plan_for(user_id).limits.historyDays
    return None if days is None else datetime.now(timezone.utc) - timedelta(days=days)


def _with_session(fn, *args):  # noqa: ANN001, ANN202
    with session_scope() as session:
        return fn(session, *args)


# ---- metering -----------------------------------------------------------------


def record_query(user_id: str, *, calls: list[ModelCall], rows_returned: int, source_name: str | None) -> None:
    """Write the ledger row for one answered question, then warn if it is time.

    The cost is what the model calls reported in tokens, at the configured
    per-model prices — never an estimate. A failure here is logged and
    swallowed: the user already has their answer, and losing one ledger row is
    better than losing the answer over it.
    """
    usd = cost_usd(calls)
    ars = usd * get_settings().usd_ars_rate
    try:
        if pool.is_configured():
            with session_scope() as session:
                repo.record_query(
                    session, user_id, rows_returned=rows_returned, cost_usd=usd, cost_ars=ars, source_name=source_name
                )
        else:
            mem.record_query(user_id, rows_returned=rows_returned, cost_usd=usd, cost_ars=ars, source_name=source_name)
        _notify_thresholds(user_id)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("could not record the ledger row for user %s", user_id)


def _notify_thresholds(user_id: str) -> None:
    """Notify on crossing 80 % and 100 % of the quota, and the overage cap.

    "Crossing" is exact equality on the count just reached, so each fires once
    a month without storing that it fired: the count only goes up within a
    month, one at a time.
    """
    sub = get_subscription(user_id)
    used, quota = sub.usage.queries.used, sub.usage.queries.limit
    if quota is None:
        return
    pro = sub.overage.available

    if used == int(quota * WARN_AT):
        body = (
            f"Usaste {used} de las {quota} consultas de tu plan Free este mes."
            if not pro
            else f"Usaste {used:,} de las {quota:,} consultas de tu plan este mes.".replace(",", ".")
            + (
                " Al llegar al límite seguís con consultas adicionales."
                if sub.overage.enabled
                else " Activá las consultas adicionales para no quedarte sin respuestas."
            )
        )
        notifications.notify(user_id, tone="warn", title="Estás cerca del límite de tu plan", body=body, action=_TO_ACCOUNT)
    elif used == quota:
        body = (
            f"Usaste tus consultas de este mes. Pasá a Pro para tener {_pro_quota()} consultas mensuales."
            if not pro
            else "Usaste las consultas incluidas en tu plan este mes."
            + (
                f" Las siguientes se cobran a {_ars(sub.overage.pricePerQueryArs or 0)} cada una."
                if sub.overage.enabled
                else " Activá las consultas adicionales para seguir preguntando."
            )
        )
        notifications.notify(user_id, tone="err" if not sub.overage.enabled else "info",
                             title="Llegaste al límite de tu plan", body=body, action=_TO_ACCOUNT)
    elif sub.overage.enabled and sub.overage.capArs is not None:
        price = sub.overage.pricePerQueryArs or 0.0
        # The query that made the next one unaffordable is the one that reached the cap.
        if sub.overage.amountArs <= sub.overage.capArs < sub.overage.amountArs + price:
            notifications.notify(
                user_id,
                tone="err",
                title="Llegaste a tu tope de consultas adicionales",
                body=f"Gastaste {_ars(sub.overage.amountArs)} en consultas adicionales este mes. "
                "Subí el tope en Cuenta si necesitás seguir.",
                action=_TO_ACCOUNT,
            )
