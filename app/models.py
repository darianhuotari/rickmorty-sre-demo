from sqlalchemy import Column, Integer, String, DateTime, func
from app.db import Base

class Character(Base):
    __tablename__ = "characters"
    id = Column(Integer, primary_key=True, index=True)  # Rick & Morty global ID
    name = Column(String, index=True, nullable=False)
    status = Column(String, nullable=False)
    species = Column(String, nullable=False)
    origin = Column(String, index=True, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
