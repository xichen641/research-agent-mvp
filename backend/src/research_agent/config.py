from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ResearchAgent"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://research:research@postgres:5432/research"
    redis_url: str = "redis://redis:6379/0"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "research"
    minio_secret_key: str = "research-secret"
    minio_bucket: str = "research-agent"
    web_origin: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
