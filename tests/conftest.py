# --- keep this shim at the very top ---
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# --------------------------------------

import pytest_asyncio
import json
import pathlib
import httpx

from contextlib import asynccontextmanager


# Force an in-memory SQLite for unit tests so we never touch Postgres pooling.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# Ensure no leftover pool envs confuse SQLAlchemy in tests
os.environ.pop("DB_POOL_SIZE", None)
os.environ.pop("DB_MAX_OVERFLOW", None)

from app import db  # noqa: E402

# Make sure the already-imported module uses our test URL
db.configure_engine(os.environ["DATABASE_URL"])

import app.main as app_main  # patch names bound inside main.py

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest_asyncio.fixture(autouse=True)
async def memory_db_and_overrides(monkeypatch):
    """
    Use an in-memory SQLite DB for all tests.
    - Ensure schema is created.
    - Make FastAPI dependencies pull sessions from this engine.
    - Replace app lifespan so TestClient startup doesn't run ingest.
    """
    # Point SQLAlchemy at an in-memory DB and create tables
    db.configure_engine("sqlite+aiosqlite:///:memory:")
    await db.init_db()

    # Dependency override: ensure request handlers use this in-memory session
    async def override_get_session():
        async with db.SessionLocal() as session:
            yield session

    app_main.app.dependency_overrides[app_main.get_session] = override_get_session

    # Lifespan override: init schema, skip ingest
    @asynccontextmanager
    async def test_lifespan(_app):
        # Tables already created above; call again safely (idempotent)
        await db.init_db()
        yield

    # Replace the router's lifespan context so TestClient won't trigger ingest
    monkeypatch.setattr(
        app_main.app.router, "lifespan_context", test_lifespan, raising=False
    )

    yield

    # Cleanup dependency override
    app_main.app.dependency_overrides.pop(app_main.get_session, None)


@pytest_asyncio.fixture
async def test_app():
    # Use the already-imported app with your in-memory DB overrides + test lifespan
    from app.main import app as _app

    yield _app


@pytest_asyncio.fixture
async def test_client(test_app):
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c:
        yield c
