"""FastAPI app, lifespan bootstrap, and HTTP routes.

Defines the application instance, startup sequence (schema init + initial ingest),
and the public REST endpoints:

- GET /            -> redirect to Swagger UI (/docs)
- GET /healthcheck -> deep health (upstream and DB)
- GET /characters  -> paginated/sorted characters from the DB
"""

import math
import os
import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

# Rate limiting (required)
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from . import api, crud, ingest
from .db import get_session, init_db
from .schemas import CharactersPage, HealthcheckOut, ProblemDetail

# ---------------------------------------------------------------------
# App + rate limiter
# ---------------------------------------------------------------------

# Default per-IP rate; override in env (e.g., RATE_LIMIT="20/second")
DEFAULT_RATE = os.getenv("RATE_LIMIT", "100/second")

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[DEFAULT_RATE],  # applies to all routes unless decorated differently
)

app = FastAPI(title="Rick & Morty Characters", version="0.6.0")
app.state.limiter = limiter

# map common titles
_STATUS_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
}


def _problem(
    status: int,
    title: str | None = None,
    detail: str | None = None,
    instance: str | None = None,
) -> JSONResponse:
    """Return an RFC7807 problem+json response."""
    body = {
        "type": "about:blank",
        "title": title or _STATUS_TITLES.get(status, "Error"),
        "status": status,
        "detail": detail,
        "instance": instance,
    }
    return JSONResponse(
        status_code=status, content=body, media_type="application/problem+json"
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_req: Request, exc: HTTPException):
    # Normalize all HTTPExceptions to problem+json. Keep your 400 detail string intact.
    detail = exc.detail if isinstance(exc.detail, str) else None
    return _problem(
        status=exc.status_code, title=_STATUS_TITLES.get(exc.status_code), detail=detail
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_req: Request, exc: RequestValidationError):
    msg = exc.errors()[0]["msg"] if exc.errors() else "Validation error"
    return _problem(status=422, title=_STATUS_TITLES[422], detail=msg)


@app.exception_handler(RateLimitExceeded)
async def ratelimit_exception_handler(_req: Request, exc: RateLimitExceeded):
    # SlowAPI’s message varies; normalize it here.
    return _problem(
        status=429, title=_STATUS_TITLES[429], detail=f"Rate limit exceeded: {exc}"
    )


app.add_middleware(SlowAPIMiddleware)

# ---------------------------------------------------------------------
# Lifespan + background refresh
# ---------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize schema, seed (once), and run refresh daemon."""
    await init_db()
    # Seed once if empty
    async for session in get_session():
        await ingest.initial_sync_if_empty(session)
        break

    # Optional background refresher (good for dev; prod can use a CronJob)
    enabled = os.getenv("REFRESH_WORKER_ENABLED", "1") not in ("0", "false", "False")
    stop_event = asyncio.Event()
    task = None

    if enabled:
        interval = float(os.getenv("REFRESH_INTERVAL", "300"))

        async def _refresher():
            while not stop_event.is_set():
                # one short-lived session per iteration
                async for s in get_session():
                    try:
                        await ingest.refresh_if_stale(s)
                    except Exception:
                        # keep going; we don't want the task to die
                        pass
                    break
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue

        task = asyncio.create_task(_refresher())

    try:
        yield
    finally:
        stop_event.set()
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app.router.lifespan_context = lifespan

_problem_resp = {
    "application/problem+json": {"schema": ProblemDetail.model_json_schema()},
}

# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def root(request: Request):
    """Redirect the root path to the interactive API docs (/docs)."""
    return RedirectResponse(url=app.docs_url or "/docs", status_code=307)


@app.get(
    "/healthcheck",
    response_model=HealthcheckOut,
    responses={429: {"content": _problem_resp, "model": ProblemDetail}},
)
@limiter.limit(DEFAULT_RATE)
async def healthcheck(request: Request, session: AsyncSession = Depends(get_session)):
    """Deep health check for upstream API and database.

    Verifies upstream reachability, DB connectivity / refresh recency. Also returns the total
    character count as a simple business metric.
    """
    upstream_ok = await api.quick_upstream_probe()

    db_ok = True
    total = 0
    try:
        total = await crud.count_characters(session)
    except Exception:
        db_ok = False

    return {
        "status": "ok" if (upstream_ok and db_ok) else "degraded",
        "upstream_ok": upstream_ok,
        "db_ok": db_ok,
        "character_count": total,
        "last_refresh_age": ingest.last_refresh_age(),
    }


@app.get(
    "/characters",
    response_model=CharactersPage,
    responses={
        400: {"content": _problem_resp, "model": ProblemDetail},
        422: {"content": _problem_resp, "model": ProblemDetail},
        429: {"content": _problem_resp, "model": ProblemDetail},
    },
)
@limiter.limit(DEFAULT_RATE)
async def characters(
    request: Request,
    sort: str = Query("id", pattern=r"^(id|name)$"),
    order: str = Query("asc", pattern=r"^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Return paginated, sorted characters from the database.

    Raises:
        HTTPException: 400 if a query error occurs (e.g., invalid sort/order).
    """
    try:
        rows, total_count = await crud.list_characters(
            session, sort, order, page, page_size
        )
    except Exception:
        # Keep your existing error shape so current tests continue to pass
        raise HTTPException(status_code=400, detail="Invalid sort parameter or query")

    total_pages = max(1, math.ceil(total_count / page_size))

    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "results": rows,
    }
