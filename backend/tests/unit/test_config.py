import pytest
from pydantic import ValidationError

from research_agent.config import Settings


def test_embedding_dimension_parses_from_environment_string() -> None:
    settings = Settings(embedding_dimension="1536")  # type: ignore[arg-type]

    assert settings.embedding_dimension == 1536


def test_embedding_dimension_rejects_unsupported_vector_size() -> None:
    with pytest.raises(ValidationError, match="1536"):
        Settings(embedding_dimension="1024")  # type: ignore[arg-type]


def test_llm_timeout_is_long_enough_for_evidence_backed_generation() -> None:
    settings = Settings(llm_timeout_seconds="180")  # type: ignore[arg-type]

    assert settings.llm_timeout_seconds == 180.0


def test_llm_timeout_rejects_non_positive_values() -> None:
    with pytest.raises(ValidationError, match="positive"):
        Settings(llm_timeout_seconds=0)


def test_memory_recent_window_must_fit_inside_context_budget() -> None:
    with pytest.raises(ValidationError, match="smaller"):
        Settings(
            memory_max_context_tokens=1_000,
            memory_recent_message_tokens=1_000,
        )
