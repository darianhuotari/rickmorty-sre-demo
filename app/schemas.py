"""Pydantic schemas for API request/response bodies."""

from typing import Optional, List, Literal
from pydantic import BaseModel


class CharacterOut(BaseModel):
    id: int
    name: str
    status: Optional[str] = None
    species: Optional[str] = None
    origin: Optional[str] = None
    image: Optional[str] = None
    url: Optional[str] = None


class CharactersPage(BaseModel):
    page: int
    page_size: int
    total_count: int
    total_pages: int
    has_prev: bool
    has_next: bool
    results: List[CharacterOut]


class HealthcheckOut(BaseModel):
    status: Literal["ok", "degraded"]
    upstream_ok: bool
    db_ok: bool
    character_count: int
    last_refresh_age: Optional[float] = None


class ProblemDetail(BaseModel):
    """RFC 7807-style problem response (simplified)."""

    type: str = "about:blank"
    title: str
    status: int
    detail: Optional[str] = None
    instance: Optional[str] = None
