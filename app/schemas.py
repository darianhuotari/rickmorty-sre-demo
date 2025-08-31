from pydantic import BaseModel

class CharacterOut(BaseModel):
    id: int
    name: str
    status: str
    species: str
    origin: str