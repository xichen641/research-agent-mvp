from functools import lru_cache
from typing import Literal

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
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: Literal[1536] = 1536
    embedding_batch_size: int = 32

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
