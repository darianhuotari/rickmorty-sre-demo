import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://rm_user:rm_pass@localhost:5432/rm_db"
)

engine_kwargs = dict(
    future=True,
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
)

if DATABASE_URL.startswith("sqlite"):
    # needed for FastAPI TestClient / threads
    engine_kwargs["connect_args"] = {"check_same_thread": False}
    # no pool args with SQLite
    engine_kwargs.pop("pool_size", None)
    engine_kwargs.pop("max_overflow", None)
    engine_kwargs.pop("pool_pre_ping", None)

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()