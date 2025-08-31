import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.db import get_db
from app.schemas import CharacterOut
from app.crud import list_characters, upsert_characters, count_characters
from app.clients import fetch_filtered_characters
from app.cache import cache_get, cache_set

bootstrap_lock = asyncio.Lock()
_bootstrapped = False


router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

# Attach rate limit exception handler for the parent FastAPI app in main.py
@router.get("/characters", response_model=list[CharacterOut])
@limiter.limit("60/minute")
async def get_characters(
    request: Request,
    sort_by: str = Query("id", pattern="^(id|name)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    global _bootstrapped
    if not _bootstrapped and count_characters(db) == 0:
        async with bootstrap_lock:
            if not _bootstrapped and count_characters(db) == 0:
                try:
                    items = await fetch_filtered_characters()
                    upsert_characters(db, items)
                    _bootstrapped = True
                except Exception as e:
                    raise HTTPException(status_code=503, detail=f"Bootstrap sync failed: {e}")

    key = ("characters", sort_by, order, limit, offset)
    cached = cache_get(key)
    if cached is not None:
        return cached

    # 👇 Wrap DB access robustly
    try:
        rows = list_characters(db, sort_by, order, limit, offset)
    except Exception as e:
        # Always map to 503
        raise HTTPException(status_code=503, detail=f"DB query failed: {e}")

    payload = [CharacterOut.model_validate(r, from_attributes=True).model_dump() for r in rows]
    cache_set(key, payload)
    return payload

@router.post("/sync")
async def sync(db: Session = Depends(get_db)):
    items = await fetch_filtered_characters()
    upserted = upsert_characters(db, items)
    total = count_characters(db)
    return {"upserted": upserted, "total": total}