from sqlalchemy.orm import Session
from sqlalchemy import select, asc, desc
from app.models import Character

def list_characters(db: Session, sort_by: str, order: str, limit: int, offset: int):
    if db is None:
        raise RuntimeError("DB session unavailable")

    sort_col = Character.id if sort_by == "id" else Character.name
    sorter = asc(sort_col) if order == "asc" else desc(sort_col)
    stmt = select(Character).order_by(sorter).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()

def upsert_characters(db: Session, items: list[dict]):
    if db is None:
        raise RuntimeError("DB session unavailable")

    upserted = 0
    for item in items:
        obj = db.get(Character, item["id"])
        if obj:
            obj.name = item["name"]
            obj.status = item["status"]
            obj.species = item["species"]
            obj.origin = item["origin"]
        else:
            obj = Character(**item)
            db.add(obj)
        upserted += 1
    db.commit()
    return upserted

def count_characters(db: Session):
    if db is None:
        raise RuntimeError("DB session unavailable")

    return db.query(Character).count()