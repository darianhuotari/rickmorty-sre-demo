"""DB bootstrap smoke tests.

Ensures the async engine and session factory are correctly configured and that
we can execute a trivial statement using a proper async session context.
"""

import pytest
from sqlalchemy import text
from app import db


@pytest.mark.asyncio
async def test_configure_engine_and_init_db_creates_tables():
    """Create an in-memory engine, init schema, and execute a trivial query."""
    db.configure_engine("sqlite+aiosqlite:///:memory:")
    await db.init_db()
    async with db.SessionLocal() as s:
        await s.execute(text("select 1"))
