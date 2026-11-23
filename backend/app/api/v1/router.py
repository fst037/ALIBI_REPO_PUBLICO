from fastapi import APIRouter

from app.api.v1.routes import (
    account,
    auth,
    billing,
    conversations,
    dashboards,
    datasets,
    exports,
    folders,
    health,
    library,
    notifications,
    plans,
    uploads,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(account.router)
router.include_router(plans.router)
router.include_router(billing.router)
router.include_router(notifications.router)
router.include_router(uploads.router)
router.include_router(datasets.router)
router.include_router(folders.router)
router.include_router(conversations.router)
router.include_router(library.router)
router.include_router(dashboards.router)
router.include_router(exports.router)
