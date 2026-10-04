"""
AgentGuard platform API: PostgreSQL-backed governance, durable approvals and a same-origin
dashboard.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from apps.api.rate_limit import limiter
from apps.api.routers.agents import router as agents_router
from apps.api.routers.approvals import router as approvals_router
from apps.api.routers.audit import router as audit_router
from apps.api.routers.auth import router as auth_router
from apps.api.routers.connectors.general import router as connectors_router
from apps.api.routers.connectors.github import router as github_router
from apps.api.routers.connectors.gmail import router as gmail_router
from apps.api.routers.connectors.slack import router as slack_router
from apps.api.routers.overview import router as overview_router
from apps.api.routers.policies import router as policies_router
from apps.api.routers.tenant import router as tenant_router
from infrastructure.config import settings
from infrastructure.database.session import check_database_connection

http_logger = logging.getLogger("agentguard.http")
http_logger.setLevel(logging.INFO)
http_logger.propagate = False
if not http_logger.handlers:
    http_logger.addHandler(logging.StreamHandler())

app = FastAPI(title="AgentGuard Platform API", version="0.4.0")

app.state.limiter = limiter
# slowapi's handler is typed to take exactly RateLimitExceeded; mypy only
# sees Starlette's broader Callable[[Request, Exception], Response] shape
# for add_exception_handler's second argument. The narrower type is
# correct at runtime -- Starlette dispatches by the registered exception
# class, so this handler is only ever called with a RateLimitExceeded.
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(tenant_router)
app.include_router(policies_router)
app.include_router(agents_router)
app.include_router(connectors_router)
app.include_router(gmail_router)
app.include_router(github_router)
app.include_router(slack_router)
app.include_router(approvals_router)
app.include_router(audit_router)


@app.get("/health")
async def health() -> JSONResponse:
    db_ok = await check_database_connection()
    if not db_ok:
        return JSONResponse(
            status_code=503, content={"status": "degraded", "database": "unreachable"}
        )
    return JSONResponse({"status": "ok", "database": "connected"})


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": [{"loc": e["loc"], "type": e["type"]} for e in exc.errors()]},
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    origin = request.headers.get("origin")
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and origin
        and origin not in settings.cors_allowed_origins_list
    ):
        return JSONResponse(status_code=403, content={"detail": "Origin not allowed"})
    try:
        content_length = int(request.headers.get("content-length", "0"))
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
    if content_length < 0:
        return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
    if content_length > 131072:
        return JSONResponse(status_code=413, content={"detail": "Request too large"})
    request_id = uuid.uuid4().hex
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        response = JSONResponse(
            status_code=500, content={"detail": "Internal error", "request_id": request_id}
        )
    route = request.scope.get("route")
    logging.getLogger("agentguard.http").info(
        json.dumps(
            {
                "request_id": request_id,
                "method": request.method,
                "route": getattr(route, "path", "unmatched"),
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
        )
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


app.include_router(overview_router)
