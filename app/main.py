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

from typing import Any
from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.exc import (
    OperationalError,
    InterfaceError,
    DatabaseError,
    ProgrammingError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from . import api, crud, ingest
from .db import get_session, init_db, wait_for_db
from .page_cache import page_cache
from .schemas import CharactersPage, HealthcheckOut, ProblemDetail
from .metrics import (
    install as install_metrics,
    observe_health,
    record_cache_hit,
    record_cache_put,
    record_cache_error,
)
from .logging_config import configure_logging

configure_logging()
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# App
# ---------------------------------------------------------------------

app = FastAPI(title="Rick & Morty Characters", version="0.6.0")

install_metrics(app)
log.info("Metrics installed")

_STATUS_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    422: "Unprocessable Entity",
    500: "Internal Server Error",
    503: "Service Unavailable",
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
    body = {
        "type": "about:blank",
        "title": _STATUS_TITLES.get(exc.status_code, "Error"),
        "status": exc.status_code,
        "detail": detail,
        "instance": None,
    }
    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        media_type="application/problem+json",
        headers=exc.headers or None,
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
    except Exception as e:
        log.error("startup.db_wait_failed error=%r", e)
        # Let startup fail so K8s can restart us (or backoff)
        raise

    # 2) Create/upgrade schema
    await init_db()
    log.info("startup.db_init complete")

    # 3) Seed once if empty (guarded by advisory-lock in ingest)
    async for session in get_session():
        n = await ingest.initial_sync_if_empty(session)
        log.info("startup.initial_sync_if_empty upserted=%d", n)
        break

    # 4) Optional background refresher (we could move this to a cron /
    # dedicated microservice in prod)
    enabled = os.getenv("REFRESH_WORKER_ENABLED", "1") not in ("0", "false", "False")
    stop_event = asyncio.Event()
    task = None

    if enabled:
        interval = float(os.getenv("REFRESH_INTERVAL", "300"))
        log.info("refresh_worker enabled=true interval=%.3fs", interval)

        async def _refresher():
            while not stop_event.is_set():
                async for s in get_session():
                    try:
                        n = await ingest.refresh_if_stale(s)
                        if n:
                            log.info("refresh_worker.cycle upserted=%d", n)
                    except Exception as exc:
                        # keep going; we don't want the task to die
                        log.warning("refresh_worker.error error=%r", exc)
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

_common_error_responses: dict[int | str, dict[str, Any]] = {
    422: {"content": _problem_resp, "model": ProblemDetail},
    500: {
        "content": _problem_resp,
        "model": ProblemDetail,
        "description": "Internal server error.",
    },
    503: {
        "content": _problem_resp,
        "model": ProblemDetail,
        "description": "Service temporarily unavailable (e.g., database).",
        "headers": {
            "Retry-After": {
                "schema": {"type": "string"},
                "description": "Seconds or HTTP-date indicating when to retry.",
            }
        },
    },
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
)
async def healthcheck(request: Request, session: AsyncSession = Depends(get_session)):
    """Deep health check for upstream API and database."""
    upstream_ok = await api.quick_upstream_probe()

    db_ok = True
    total = 0
    try:
        total = await crud.count_characters(session)
    except Exception as exc:
        db_ok = False
        log.debug("route.healthcheck.db_error error=%r", exc)
    status = "ok" if (upstream_ok and db_ok) else "degraded"
    log.info(
        "route.healthcheck status=%s upstream_ok=%s db_ok=%s character_count=%d",
        status,
        upstream_ok,
        db_ok,
        total,
    )

    age = ingest.last_refresh_age()
    observe_health(db_ok=db_ok, upstream_ok=upstream_ok, age=age)

    return {
        "status": status,
        "upstream_ok": upstream_ok,
        "db_ok": db_ok,
        "character_count": total,
        "last_refresh_age": ingest.last_refresh_age(),
    }


@app.get(
    "/characters",
    response_model=CharactersPage,
    responses=_common_error_responses,
)
async def characters(
    request: Request,
    sort: str = Query("id", pattern=r"^(id|name)$"),
    order: str = Query("asc", pattern=r"^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Return paginated, sorted characters from the database (LRU+TTL cached).

    The per-pod cache keys on (sort, order, page, page_size). We:
      1) Attempt a cache hit.
      2) If miss, acquire a per-key lock (singleflight).
      3) Re-check cache after acquiring the lock.
      4) On miss, query the DB, build the response, store, and return.

    Args:
        request: Incoming FastAPI request (unused; reserved for future).
        sort: Sort field, one of {"id","name"}.
        order: Sort order, one of {"asc","desc"}.
        page: 1-based page number.
        page_size: Items per page (1–100).
        session: Async SQLAlchemy session.

    Returns:
        CharactersPage JSON object (possibly served from the cache).
    """
    key = page_cache.key(sort, order, page, page_size)

    # -------- Cache fast-path (GUARDED) --------
    try:
        cached = page_cache.get(key)
    except Exception as exc:
        record_cache_error("get")
        log.warning("route.characters page_cache_get_error key=%s err=%r", key, exc)
        cached = None

    if cached is not None:
        record_cache_hit()
        log.info("route.characters cache_hit key=%s", key)
        return cached

    # -------- Singleflight around DB work --------
    lock = page_cache.lock_for(key)
    async with lock:
        # Re-check after acquiring the lock (GUARDED)
        try:
            cached = page_cache.get(key)
        except Exception as exc:
            record_cache_error("get")
            log.debug(
                "route.characters page_cache_get_after_lock_error key=%s err=%r",
                key,
                exc,
            )
            cached = None

        if cached is not None:
            record_cache_hit()
            log.debug("route.characters cache_hit_after_lock key=%s", key)
            return cached

        # Miss -> query DB
        try:
            rows, total_count = await crud.list_characters(
                session, sort, order, page, page_size
            )

        except ValueError as exc:
            # somehow the client sent an invalid sort/order -> 400
            log.info(
                "route.characters client_error sort=%s order=%s page=%d page_size=%d err=%r",
                sort,
                order,
                page,
                page_size,
                exc,
            )
            raise HTTPException(
                status_code=400, detail="Invalid sort parameter or query"
            ) from exc

        except (OperationalError, InterfaceError, asyncio.TimeoutError) as exc:
            # Transient DB/unavailable -> 503
            log.warning(
                "route.characters db_unavailable sort=%s order=%s page=%d page_size=%d error=%r",
                sort,
                order,
                page,
                page_size,
                exc,
            )
            raise HTTPException(
                status_code=503,
                detail="Database temporarily unavailable; please try again.",
                headers={"Retry-After": "5"},
            ) from exc

        except (ProgrammingError, DatabaseError) as exc:
            # Server-side DB bug/schema issue -> 500
            log.error("route.characters db_error error=%r", exc, exc_info=True)
            raise HTTPException(status_code=500, detail="Database error.") from exc

        except Exception as exc:
            # Anything else unexpected -> 500
            log.exception("route.characters unexpected_error")
            raise HTTPException(
                status_code=500, detail="Internal server error."
            ) from exc

        total_pages = math.ceil(total_count / page_size) if total_count else 0
        out_of_range = (total_pages > 0 and page > total_pages) or (
            total_pages == 0 and page > 1
        )

        log.info(
            "route.characters sort=%s order=%s page=%d page_size=%d returned=%d total=%d pages=%d out_of_range=%s",
            sort,
            order,
            page,
            page_size,
            len(rows),
            total_count,
            total_pages,
            out_of_range,
        )

        resp = {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_prev": (page > 1) and not out_of_range,
            "has_next": (page < total_pages),
            "out_of_range": out_of_range,
            "results": [] if out_of_range else rows,
        }

        try:
            page_cache.put(key, resp)
            record_cache_put()
        except Exception as exc:
            record_cache_error("put")
            log.warning("route.characters page_cache_put_error key=%s err=%r", key, exc)

        return resp
