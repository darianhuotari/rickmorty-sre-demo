from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Postgres (override via env)
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    DB_NAME: str = "rickmorty"

    # API
    CACHE_TTL_SECONDS: int = 60  # in-process TTL cache
    RATE_LIMIT: str = "60/minute"  # per client IP

    class Config:
        env_file = ".env"

settings = Settings()