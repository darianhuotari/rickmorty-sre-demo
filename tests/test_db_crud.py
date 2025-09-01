"""CRUD behavior tests.

Covers:
* Counting rows.
* Portable per-row upsert via `session.merge()`.
* SQL-level sorting and OFFSET/LIMIT pagination.
"""

import pytest
from app import db, crud


@pytest.mark.asyncio
async def test_upsert_and_count_and_list_paging_and_sort():
    """Insert/update a small set, verify counts and paginated ordering."""
    db.configure_engine("sqlite+aiosqlite:///:memory:")
    await db.init_db()
    async with db.SessionLocal() as s:
        assert await crud.count_characters(s) == 0

        items = [
            {
                "id": 2,
                "name": "Summer Smith",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (Replacement Dimension)",
                "image": None,
                "url": None,
            },
            {
                "id": 1,
                "name": "Beth Smith",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (Replacement Dimension)",
                "image": None,
                "url": None,
            },
            {
                "id": 3,
                "name": "Morty Smith",
                "status": "Alive",
                "species": "Human",
                "origin": "Earth (C-137)",
                "image": None,
                "url": None,
            },
        ]
        n = await crud.upsert_characters(s, items)
        assert n == 3
        assert await crud.count_characters(s) == 3

        rows, total = await crud.list_characters(
            s, sort="id", order="asc", page=1, page_size=2
        )
        assert total == 3
        assert [r["id"] for r in rows] == [1, 2]

        rows, _ = await crud.list_characters(
            s, sort="id", order="asc", page=2, page_size=2
        )
        assert [r["id"] for r in rows] == [3]

        rows, _ = await crud.list_characters(
            s, sort="name", order="desc", page=1, page_size=3
        )
        assert [r["name"] for r in rows] == [
            "Summer Smith",
            "Morty Smith",
            "Beth Smith",
        ]
