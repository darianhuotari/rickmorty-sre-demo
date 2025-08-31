from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import select, asc, desc
from app.models import Character

def upsert_characters(db: Session, items: List[dict]) -> int:
    # Idempotent upsert by primary key
    count = 0
    for it in items:
        obj = db.get(Character, it["id"])
        if obj is None:
            obj = Character(**it)
            db.add(obj)
        else:
            obj.name = it["name"]
            obj.status = it["status"]
            obj.species = it["species"]
            obj.origin = it["origin"]
        count += 1
    db.commit()
    return count

def list_characters(db: Session, sort_by: str, order: str, limit: int, offset: int):
    sort_col = Character.id if sort_by == "id" else Character.name
    sorter = asc(sort_col) if order == "asc" else desc(sort_col)
    stmt = select(Character).order_by(sorter).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()

def count_characters(db: Session) -> int:
    return db.query(Character).count()