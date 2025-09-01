"""Data access helpers (CRUD) for Character.

Holds read/write/query logic so HTTP handlers can stay thin. All functions are
async and accept an `AsyncSession`. Queries are written to be portable across
SQLite (tests/dev) and Postgres (dev/prod).
"""

from typing import Iterable, List, Dict, Any, Tuple
from sqlalchemy import select, func, asc, desc
from sqlalchemy.ext.asyncio import AsyncSession
from .models import Character


def _row_to_dict(c: Character) -> Dict[str, Any]:
    """Map a `Character` ORM row to the API response dict shape.

    Args:
        c: A `Character` ORM instance.

    Returns:
        A dict containing the public fields for the character.
    """
    return {
        "id": c.id,
        "name": c.name,
        "status": c.status,
        "species": c.species,
        "origin": c.origin,
        "image": c.image,
        "url": c.url,
    }


async def count_characters(session: AsyncSession) -> int:
    """Return the total number of characters in the database.

    Args:
        session: Active async SQLAlchemy session.

    Returns:
        Row count as an integer.
    """
    q = select(func.count()).select_from(Character)
    res = await session.execute(q)
    return int(res.scalar_one())


async def upsert_characters(
    session: AsyncSession, items: Iterable[Dict[str, Any]]
) -> int:
    """Insert or update characters (portable upsert) and commit.

    Uses `session.merge()` per item for cross-database portability (SQLite & Postgres).
    Returns the number of processed items (inserted or updated).

    Args:
        session: Active async SQLAlchemy session.
        items: Iterable of character dicts matching the `Character` schema.

    Returns:
        The number of items processed.
    """
    n = 0
    for it in items:
        await session.merge(Character(**it))
        n += 1
    await session.commit()
    return n


async def list_characters(
    session: AsyncSession,
    sort: str,
    order: str,
    page: int,
    page_size: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """Return a page of characters, sorted in SQL, plus the total row count.

    Args:
        session: Active async SQLAlchemy session.
        sort: Sort key: "id" or "name".
        order: Sort order: "asc" or "desc".
        page: 1-based page number (>= 1).
        page_size: Number of items per page.

    Returns:
        A tuple of (rows, total_count):
          * rows: List of dicts (already paginated & sorted).
          * total_count: Total number of rows across all pages.
    """
    order_func = asc if order == "asc" else desc
    sort_col = Character.name if sort == "name" else Character.id

    # total count
    q_total = select(func.count()).select_from(Character)
    total = int((await session.execute(q_total)).scalar_one())

    # page slice
    q = (
        select(Character)
        .order_by(order_func(sort_col))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    res = await session.execute(q)
    rows = [_row_to_dict(row[0]) for row in res.fetchall()]
    return rows, total
