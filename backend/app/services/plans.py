"""`plans` domain service — swap point for the public plan catalog."""

from app.db import pool
from app.db.engine import session_scope
from app.repositories import plans as repo
from app.schemas.plans import Plan, PlanId
from app.services import plans_memory as mem


def list_plans() -> list[Plan]:
    if pool.is_configured():
        with session_scope() as session:
            return repo.list_plans(session)
    return mem.list_plans()


def get_plan(plan_id: PlanId) -> Plan | None:
    if pool.is_configured():
        with session_scope() as session:
            return repo.get_plan(session, plan_id)
    return mem.get_plan(plan_id)
