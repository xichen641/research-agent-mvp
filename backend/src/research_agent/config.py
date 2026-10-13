from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
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
    memory_max_context_tokens: int = 6_000
    memory_recent_message_tokens: int = 2_500
    chat_run_lease_seconds: int = 1_800
    chat_recovery_batch_size: int = 20
    chat_evidence_limit: int = 6
    arxiv_api_url: str = "https://export.arxiv.org/api/query"
    arxiv_user_agent: str = "ResearchAgent/0.1 (local research platform)"
    arxiv_timeout_seconds: float = 30.0
    arxiv_cache_hours: int = 24
    arxiv_max_pdf_bytes: int = 50 * 1024 * 1024

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

    @model_validator(mode="after")
    def validate_memory_budgets(self) -> "Settings":
        if self.memory_max_context_tokens <= 0:
            raise ValueError("memory_max_context_tokens must be positive")
        if self.memory_recent_message_tokens <= 0:
            raise ValueError("memory_recent_message_tokens must be positive")
        if self.memory_recent_message_tokens >= self.memory_max_context_tokens:
            raise ValueError(
                "memory_recent_message_tokens must be smaller than "
                "memory_max_context_tokens"
            )
        if self.chat_run_lease_seconds < 300:
            raise ValueError("chat_run_lease_seconds must be at least 300")
        if self.chat_recovery_batch_size <= 0:
            raise ValueError("chat_recovery_batch_size must be positive")
        if not 1 <= self.chat_evidence_limit <= 20:
            raise ValueError("chat_evidence_limit must be between 1 and 20")
        if self.arxiv_timeout_seconds <= 0:
            raise ValueError("arxiv_timeout_seconds must be positive")
        if self.arxiv_cache_hours <= 0:
            raise ValueError("arxiv_cache_hours must be positive")
        if self.arxiv_max_pdf_bytes <= 0:
            raise ValueError("arxiv_max_pdf_bytes must be positive")
        if not self.arxiv_user_agent.strip():
            raise ValueError("arxiv_user_agent must not be blank")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
