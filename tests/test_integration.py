"""Integration tests for the complete Rick and Morty API service.

Tests the full stack including:
- Database initialization and schema creation
- Data ingestion from upstream API
- Background refresh mechanism
- API endpoints with real database operations
- Health check system
"""

import os
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
import tempfile
import asyncio

from app.main import app
from app import db, ingest, api
from app.db import get_session


@pytest_asyncio.fixture
async def sqlite_db():
    """Create a temporary SQLite database file for integration tests."""
    db_file = tempfile.NamedTemporaryFile(delete=False)
    db_url = f"sqlite+aiosqlite:///{db_file.name}"

    # Configure the engine with the file-based SQLite
    engine = create_async_engine(db_url)
    db.engine = engine

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)

    yield db_url

    # Cleanup
    await engine.dispose()
    db.engine = None  # Remove global reference
    try:
        os.unlink(db_file.name)
    except PermissionError:
        pass  # On Windows, sometimes we can't delete the file due to engine not fully closed


@pytest_asyncio.fixture
async def test_app(sqlite_db):
    """Configure the FastAPI application with the test database."""
    # Update the database URL
    db.configure_engine(sqlite_db)

    # Clear any existing dependency overrides
    app.dependency_overrides = {}

    return app


@pytest_asyncio.fixture
async def test_client(test_app):
    """Create a test client with the configured application."""
    with TestClient(test_app) as client:
        yield client


@pytest.mark.asyncio
async def test_full_integration_flow(test_app, test_client):
    """Test the complete flow from ingestion to API response."""
    # First, verify empty state
    response = test_client.get("/characters")
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 0

    # Perform initial ingestion
    async for session in get_session():
        await ingest.initial_sync_if_empty(session)
        break

    # Verify data was ingested
    response = test_client.get("/characters")
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] > 0
    assert len(data["results"]) > 0

    # Test sorting
    response = test_client.get("/characters?sort=name&order=desc")
    assert response.status_code == 200
    data = response.json()
    names = [char["name"] for char in data["results"]]
    assert names == sorted(names, reverse=True)


@pytest.mark.asyncio
async def test_background_refresh(test_app, test_client):
    """Test the background refresh mechanism."""
    # Set a very short refresh interval for testing
    os.environ["REFRESH_INTERVAL"] = "1"
    os.environ["REFRESH_WORKER_ENABLED"] = "1"

    # Start the app with background refresh
    async with test_app.router.lifespan_context(test_app):
        # Initial state
        response = test_client.get("/healthcheck")
        assert response.status_code == 200
        initial_data = response.json()

        # Wait for a refresh cycle
        await asyncio.sleep(2)

        # Check refresh occurred
        response = test_client.get("/healthcheck")
        assert response.status_code == 200
        refreshed_data = response.json()

        # Verify refresh age was updated
        assert refreshed_data["last_refresh_age"] != initial_data["last_refresh_age"]


@pytest.mark.asyncio
async def test_health_check_degraded_state(test_app, test_client, monkeypatch):
    """Test health check responds correctly to degraded states."""

    # Mock the upstream API to be down
    async def mock_probe():
        return False

    monkeypatch.setattr(api, "quick_upstream_probe", mock_probe)

    # Check health status
    response = test_client.get("/healthcheck")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["upstream_ok"] is False


@pytest.mark.asyncio
async def test_pagination_integration(test_app, test_client):
    """Test pagination works correctly with real data."""
    # Perform initial ingestion
    async for session in get_session():
        await ingest.initial_sync_if_empty(session)
        break

    # Request first page
    response = test_client.get("/characters?page=1&page_size=5")
    assert response.status_code == 200
    page1 = response.json()
    assert len(page1["results"]) == 5

    # Request second page
    response = test_client.get("/characters?page=2&page_size=5")
    assert response.status_code == 200
    page2 = response.json()
    assert len(page2["results"]) == 5

    # Verify pages contain different records
    page1_ids = {char["id"] for char in page1["results"]}
    page2_ids = {char["id"] for char in page2["results"]}
    assert not page1_ids.intersection(page2_ids)
