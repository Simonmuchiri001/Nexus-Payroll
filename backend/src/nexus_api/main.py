"""Application factory, middleware and health endpoints."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from nexus_api import __version__
from nexus_api.accounting.router import router as accounting_router
from nexus_api.benefits.router import router as benefits_router
from nexus_api.config import Settings, get_settings
from nexus_api.core.context import RequestContext, get_request_context, set_request_context
from nexus_api.core.errors import NexusError
from nexus_api.core.logging import configure_logging, get_logger
from nexus_api.database.session import check_database
from nexus_api.employees.router import router as employees_router
from nexus_api.expenses.router import router as expenses_router
from nexus_api.identity.admin_router import router as identity_admin_router
from nexus_api.identity.router import router as identity_router
from nexus_api.leave.router import router as leave_router
from nexus_api.loans.router import router as loans_router
from nexus_api.organizations.router import router as organizations_router
from nexus_api.payroll.router import router as payroll_router

logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a correlation ID, bind log context and emit one access log."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid.uuid4().hex
        set_request_context(RequestContext(request_id=request_id))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request.failed", duration_ms=round((time.perf_counter() - started) * 1000, 2)
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        logger.info(
            "request.completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


async def nexus_error_handler(request: Request, exc: NexusError) -> JSONResponse:
    context = get_request_context()
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            "request_id": context.request_id if context else None,
        },
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    context = get_request_context()
    details = [
        {
            "location": [str(part) for part in error["loc"]],
            "message": str(error["msg"]),
            "type": str(error["type"]),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_failed",
                "message": "The submitted data is invalid.",
                "details": {"fields": details},
            },
            "request_id": context.request_id if context else None,
        },
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("request.unhandled_error", error_type=type(exc).__name__)
    context = get_request_context()
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "An unexpected error occurred. Contact your administrator.",
                "details": {},
            },
            "request_id": context.request_id if context else None,
        },
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(
        resolved.log_level,
        json_logs=resolved.environment == "production",
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        logger.info(
            "application.starting",
            version=__version__,
            environment=resolved.environment,
        )
        yield
        logger.info("application.stopped")

    app = FastAPI(
        title="Nexus Enterprise API",
        version=__version__,
        description=(
            "Authoritative multi-company payroll, HR, accounting and banking API. "
            "All financial computation and state transitions are owned by this service."
        ),
        lifespan=lifespan,
        docs_url="/api/docs" if resolved.environment != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if resolved.environment != "production" else None,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=resolved.allowed_hosts)
    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )

    app.add_exception_handler(NexusError, nexus_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unexpected_error_handler)

    @app.get("/api/v1/health/live", tags=["Health"], summary="Process liveness")
    async def live() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/health/ready", tags=["Health"], summary="Database readiness")
    async def ready() -> JSONResponse:
        database_ok = await check_database()
        status = "ok" if database_ok else "unavailable"
        return JSONResponse(
            status_code=200 if database_ok else 503,
            content={"status": status, "database": database_ok, "version": __version__},
        )

    app.include_router(identity_router, prefix=resolved.api_v1_prefix)
    app.include_router(identity_admin_router, prefix=resolved.api_v1_prefix)
    app.include_router(organizations_router, prefix=resolved.api_v1_prefix)
    app.include_router(employees_router, prefix=resolved.api_v1_prefix)
    app.include_router(leave_router, prefix=resolved.api_v1_prefix)
    app.include_router(loans_router, prefix=resolved.api_v1_prefix)
    app.include_router(benefits_router, prefix=resolved.api_v1_prefix)
    app.include_router(expenses_router, prefix=resolved.api_v1_prefix)
    app.include_router(payroll_router, prefix=resolved.api_v1_prefix)
    app.include_router(accounting_router, prefix=resolved.api_v1_prefix)
    return app


app = create_app()
