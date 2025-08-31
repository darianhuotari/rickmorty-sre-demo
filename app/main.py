import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler

from app.api import router as api_router, limiter
from app.db import Base, engine, SessionLocal
from app.cache import cache_stats

APP_NAME = os.getenv("APP_NAME", "Rick & Morty Service")

app = FastAPI(title=APP_NAME)

# DB init (simple auto-create; migrations can come later)
Base.metadata.create_all(bind=engine)

# ✅ Rate limiting middleware & handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Routers
app.include_router(api_router)


@app.get("/healthcheck")
def healthcheck():
    db_ok = False
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            db_ok = True
    except SQLAlchemyError:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "checks": {
            "database": "ok" if db_ok else "error",
            "cache": cache_stats(),
        },
    }


# ✅ Global exception handler for DB-related errors
@app.exception_handler(Exception)
async def db_exception_handler(request: Request, exc: Exception):
    # Treat common DB failures uniformly as 503
    if isinstance(exc, (SQLAlchemyError, RuntimeError, AttributeError)):
        return JSONResponse(
            status_code=503,
            content={"detail": "Service unavailable: database error"},
        )
    # Let other unexpected exceptions bubble up
    raise exc