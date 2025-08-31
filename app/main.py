from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse

from . import api

app = FastAPI(title="Rick & Morty Characters", version="0.2.0")


@app.get("/", include_in_schema=False)
async def root():
    # Use FastAPI’s configured docs URL if present; fallback to "/docs"
    return RedirectResponse(url=app.docs_url or "/docs", status_code=307)


@app.get("/healthcheck")
async def healthcheck():
    upstream_ok = await api.quick_upstream_probe()
    populated, age = api.cache_info()
    return {
        "status": "ok",
        "upstream_ok": upstream_ok,
        "cache_populated": populated,
        "cache_age_sec": age,
    }


@app.get("/characters")
async def characters(
    sort: str = Query("id", pattern=r"^(id|name)$"),
    order: str = Query("asc", pattern=r"^(asc|desc)$"),
):
    items: List[Dict[str, Any]] = await api.get_characters()

    reverse = order == "desc"
    try:
        characters_sorted = sorted(
            items,
            key=(
                (lambda x: x["name"].lower()) if sort == "name" else (lambda x: x["id"])
            ),
            reverse=reverse,
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid sort parameter")

    return {"count": len(characters_sorted), "results": characters_sorted}
