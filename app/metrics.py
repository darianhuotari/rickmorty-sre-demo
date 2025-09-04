import time
from typing import Optional
from fastapi import FastAPI, Request, Response
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# --- Metric objects ---
REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=["path", "method", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_latency_seconds",
    "Request latency seconds",
    labelnames=["path", "method"],
)
CACHE_HITS = Counter(
    "page_cache_hits_total", "Characters route page cache hits", labelnames=["path"]
)
CACHE_PUTS = Counter(
    "page_cache_puts_total", "Characters route page cache writes", labelnames=["path"]
)
CACHE_ERRORS = Counter(
    "cache_errors_total", "Cache operation errors", labelnames=["cache", "op"]
)

DB_OK_G = Gauge("db_ok", "Database availability (1 ok, 0 down)")
UPSTREAM_OK_G = Gauge("upstream_ok", "Upstream availability (1 ok, 0 down)")
LAST_REFRESH_AGE_G = Gauge(
    "last_refresh_age_seconds", "Seconds since last refresh (None => absent)"
)


# --- Public helpers your routes can call ---
def record_cache_hit(path="/characters"):
    CACHE_HITS.labels(path=path).inc()


def record_cache_put(path="/characters"):
    CACHE_PUTS.labels(path=path).inc()


def record_cache_error(op: str, cache: str = "page"):
    CACHE_ERRORS.labels(cache=cache, op=op).inc()


def observe_health(db_ok: bool, upstream_ok: bool, age: Optional[float]) -> None:
    DB_OK_G.set(1 if db_ok else 0)
    UPSTREAM_OK_G.set(1 if upstream_ok else 0)
    if age is not None:
        LAST_REFRESH_AGE_G.set(age)


# --- Installation: middleware + /metrics endpoint ---
def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _metrics_mw(request: Request, call_next):
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
            status = getattr(response, "status_code", 500)
            return response
        finally:
            dur = time.perf_counter() - t0
            REQUEST_LATENCY.labels(
                path=request.url.path, method=request.method
            ).observe(dur)
            REQUESTS.labels(
                path=request.url.path,
                method=request.method,
                status=str(locals().get("status", 500)),
            ).inc()

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
