from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from sqlalchemy.exc import OperationalError

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.errors import ApiException, api_exception_handler, unhandled_exception_handler
from app.db import pool
from app.db.migrate import upgrade_to_head
from app.db.waker import db_unreachable_handler

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Local-dev convenience only (ALIBI_AUTO_MIGRATE=1 in backend/.env) — the
    # deployed Lambda never auto-migrates on cold start (see `handler` below
    # for how it migrates instead: once per deploy, via an explicit invoke,
    # not on every/concurrent cold start).
    if settings.auto_migrate and pool.is_configured():
        upgrade_to_head()
    yield


# FastAPI generates the OpenAPI schema and Swagger UI (/docs) / ReDoc (/redoc)
# automatically from the route type hints and Pydantic models below — no
# separate documentation step, no extra library.
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(ApiException, api_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
# A connection that can't be opened is usually an RDS instance that stopped
# itself while idle, not a bug — see app/db/waker.py.
app.add_exception_handler(OperationalError, db_unreachable_handler)

app.include_router(v1_router, prefix="/api/v1")

_mangum_handler = Mangum(app)

# Lambda entry point (see terraform/modules/api-lambda): handler = "app.main.handler"
#
# Migrations run here, not on cold start: backend-deploy.yml invokes this
# Lambda directly with {"_alibi_migrate": true} once per deploy, right after
# updating the function code — never automatically, since concurrent Lambda
# cold starts each trying to run `alembic upgrade head` against the shared
# RDS instance at once would be a real race, not a hypothetical one. Any
# other event (i.e. every real API Gateway request) falls through to Mangum
# unchanged.
def handler(event, context):
    if isinstance(event, dict) and event.get("_alibi_migrate"):
        if not pool.is_configured():
            return {"status": "error", "detail": "ALIBI_DATABASE_URL is not set"}
        upgrade_to_head()
        return {"status": "migrated"}
    return _mangum_handler(event, context)
