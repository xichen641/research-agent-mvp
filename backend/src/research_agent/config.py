from functools import lru_cache

from pydantic import field_validator
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
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_dimension: int = 1536
    embedding_batch_size: int = 10
    reranker_base_url: str = ""
    reranker_api_key: str = ""
    reranker_model: str = "qwen3-rerank"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("embedding_dimension")
    @classmethod
    def validate_embedding_dimension(cls, value: int) -> int:
        if value != 1536:
            raise ValueError("embedding_dimension must be 1536")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
