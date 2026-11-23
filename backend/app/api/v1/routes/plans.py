"""`plans` domain routes: public plan catalog."""

from fastapi import APIRouter

from app.core.errors import error_response
from app.schemas.plans import Plan, PlanId
from app.services import plans as store

router = APIRouter(tags=["plans"])


@router.get("/plans", response_model=list[Plan], summary="Plan catalog.")
def list_plans() -> list[Plan]:
    return store.list_plans()


@router.get("/plans/{plan_id}", response_model=Plan, summary="Plan details.")
def get_plan(plan_id: PlanId):
    plan = store.get_plan(plan_id)
    if plan is None:
        return error_response(404, "not_found", "No se encontró el plan.")
    return plan
