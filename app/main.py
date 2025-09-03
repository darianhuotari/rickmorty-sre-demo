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
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from . import api, crud, ingest
from .db import get_session, init_db, wait_for_db
from .schemas import CharactersPage, HealthcheckOut, ProblemDetail
from .logging_config import configure_logging

configure_logging()
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# App
# ---------------------------------------------------------------------

app = FastAPI(title="Rick & Morty Characters", version="0.6.0")


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
    detail = exc.detail if isinstance(exc.detail, str) else None
    return _problem(
        status=exc.status_code, title=_STATUS_TITLES.get(exc.status_code), detail=detail
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_req: Request, exc: RequestValidationError):
    msg = exc.errors()[0]["msg"] if exc.errors() else "Validation error"
    return _problem(status=422, title=_STATUS_TITLES[422], detail=msg)


# ---------------------------------------------------------------------
# Lifespan + background refresh
# ---------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: wait for DB, init schema, seed (once), start refresher."""
    # 1) Wait for the database to be reachable (cold-start friendly)
    try:
        await wait_for_db()
    except Exception as exc:
        log.error("Database is not reachable after retries: %s", exc)
        # Let startup fail so K8s can restart us (or backoff)
        raise

    # 2) Create/upgrade schema
    await init_db()

    # 3) Seed once if empty (guarded by advisory-lock in ingest)
    async for session in get_session():
        await ingest.initial_sync_if_empty(session)
        break

    # 4) Optional background refresher (prod can use a CronJob instead)
    enabled = os.getenv("REFRESH_WORKER_ENABLED", "1") not in ("0", "false", "False")
    stop_event = asyncio.Event()
    task = None

    if enabled:
        interval = float(os.getenv("REFRESH_INTERVAL", "300"))

        async def _refresher():
            while not stop_event.is_set():
                async for s in get_session():
                    try:
                        await ingest.refresh_if_stale(s)
                    except Exception as exc:
                        # keep going; we don't want the task to die
                        log.warning("Background refresh error (swallowed): %r", exc)
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
async def root(_request: Request):
    """Redirect the root path to the interactive API docs (/docs)."""
    return RedirectResponse(url=app.docs_url or "/docs", status_code=307)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Lightweight, in-process health endpoint.

    Always returns 200 if the app can serve requests.
    Safe for liveness/readiness probes without hitting DB or network.
    """
    return {"status": "ok"}


@app.get(
    "/healthcheck",
    response_model=HealthcheckOut,
    responses={429: {"content": _problem_resp, "model": ProblemDetail}},
)
async def healthcheck(request: Request, session: AsyncSession = Depends(get_session)):
    """Deep health check for upstream API and database."""
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
async def characters(
    request: Request,
    sort: str = Query("id", pattern=r"^(id|name)$"),
    order: str = Query("asc", pattern=r"^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Return paginated, sorted characters from the database."""
    try:
        rows, total_count = await crud.list_characters(
            session, sort, order, page, page_size
        )
    except Exception:
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
