"""ORM models (SQLAlchemy 2.0).

Defines the schema for local persistence of Rick & Morty data.
"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime, Text, func
from .db import Base


class Character(Base):
    """ORM model for Rick & Morty characters persisted locally.

    Columns:
        id: Integer primary key (matches upstream character ID).
        name: Character name.
        status: Life status (e.g., "Alive").
        species: Species (e.g., "Human").
        origin: Origin name (e.g., "Earth (C-137)").
        image: Optional image URL.
        url: Optional upstream resource URL.
        updated_at: Server-side timestamp for last update.
    """

    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    species: Mapped[str] = mapped_column(String(50), nullable=False)
    origin: Mapped[str] = mapped_column(String(200), nullable=False)
    image: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
