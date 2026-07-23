from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_REPOSITORY_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    app_name: str = "ResearchAgent"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://research:research@postgres:5432/research"
    redis_url: str = "redis://redis:6379/0"
    minio_endpoint: str = "minio:9000"
    minio_public_endpoint: str = "localhost:9000"
    minio_region: str = "us-east-1"
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
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen3.7-plus"
    llm_timeout_seconds: float = 180.0

    model_config = SettingsConfigDict(
        env_file=(_REPOSITORY_ENV, ".env"),
        extra="ignore",
    )

    @field_validator("embedding_dimension")
    @classmethod
    def validate_embedding_dimension(cls, value: int) -> int:
        if value != 1536:
            raise ValueError("embedding_dimension must be 1536")
        return value

    @field_validator("llm_timeout_seconds")
    @classmethod
    def validate_llm_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("llm_timeout_seconds must be positive")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
