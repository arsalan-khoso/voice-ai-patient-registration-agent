"""Application entry point: wires routers, middleware, logging and (optional) demo seed data."""
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.config import get_settings
from app.db import SessionLocal
from app.errors import register_exception_handlers
from app.logging_config import configure_logging
from app.routers import pages, patients, vapi
from app.seed import seed_demo_data

settings = get_settings()
configure_logging(settings.log_level)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.vapi_webhook_secret:
        log.warning("VAPI_WEBHOOK_SECRET is not set - the /vapi/webhook endpoint is unauthenticated")
    if settings.seed_demo_data:
        with SessionLocal() as db:
            seed_demo_data(db)
    log.info("startup complete", extra={"data": {"environment": settings.environment}})
    yield


app = FastAPI(
    title="Patient Registration Voice Agent API",
    version="1.0.0",
    description="REST API for patient records + Vapi webhook used by the phone agent.",
    lifespan=lifespan,
)
register_exception_handlers(app)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    if request.url.path != "/health":
        log.info(
            "request",
            extra={"data": {
                "id": request_id, "method": request.method, "path": request.url.path,
                "status": response.status_code, "ms": round((time.perf_counter() - started) * 1000, 1),
            }},
        )
    return response


app.include_router(pages.router)
app.include_router(patients.router)
app.include_router(vapi.router)
