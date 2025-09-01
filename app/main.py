"""FastAPI app, lifespan bootstrap, and HTTP routes.

Defines the application instance, startup sequence (schema init + initial ingest),
and the public REST endpoints:

- GET /            -> redirect to Swagger UI (/docs)
- GET /healthcheck -> deep health (upstream and DB)
- GET /characters  -> paginated/sorted characters from the DB
"""

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.responses import RedirectResponse
import math
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession

from . import api, crud, ingest
from .db import get_session, init_db

app = FastAPI(title="Rick & Morty Characters", version="0.4.0")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize schema and perform initial ingest.

    Creates tables and performs a one-time ingest if the database is empty.
    Runs before the application starts accepting requests.
    """
    await init_db()
    # (We open a short-lived session to seed)
    async for session in get_session():
        await ingest.initial_sync_if_empty(session)
        break
    yield


app.router.lifespan_context = lifespan


@app.get("/", include_in_schema=False)
async def root():
    """Redirect the root path to the interactive API docs (/docs)."""
    return RedirectResponse(url=app.docs_url or "/docs", status_code=307)


@app.get("/healthcheck")
async def healthcheck(session: AsyncSession = Depends(get_session)):
    """Deep health check for upstream API and database.

    Verifies upstream reachability and DB connectivity. Also returns the total
    character count as a simple business metric.

    Args:
        session: Database session injected by FastAPI.

    Returns:
        A JSON object with fields: status, upstream_ok, db_ok, character_count.
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
    }


@app.get("/characters")
async def characters(
    sort: str = Query("id", pattern=r"^(id|name)$"),
    order: str = Query("asc", pattern=r"^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Return paginated, sorted characters from the database.

    Args:
        sort: Sort key ("id" or "name"), default "id".
        order: Sort order ("asc" or "desc"), default "asc".
        page: 1-based page number (>= 1), default 1.
        page_size: Items per page (1..100), default 20.
        session: Database session injected by FastAPI.

    Returns:
        A JSON object with pagination metadata and page results.

    Raises:
        HTTPException: 400 if a query error occurs (e.g., invalid sort/order).
    """
    try:
        rows, total_count = await crud.list_characters(
            session, sort, order, page, page_size
        )
    except Exception:
        # bad sort/order is already constrained by Query regex; this guards unexpected errors
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
