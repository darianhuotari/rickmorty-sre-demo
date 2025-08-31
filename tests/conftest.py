import os, sys
# Ensure repo root on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ------- Set test env BEFORE importing the app -------
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("LOG_JSON", "false")
# -----------------------------------------------------

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db import Base, get_db


@pytest.fixture(scope="session")
def engine():
    # Use in-memory SQLite, shared across threads
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(engine):
    """
    Creates a new database session for a test, inside a transaction
    that is rolled back afterward.
    """
    connection = engine.connect()
    txn = connection.begin()

    TestingSessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False, future=True)
    session = TestingSessionLocal()

    try:
        yield session
    finally:
        session.close()
        txn.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _override_get_db(db_session):
    def _get_db_override():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c