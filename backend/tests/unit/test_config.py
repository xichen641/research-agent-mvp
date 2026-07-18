import pytest
from pydantic import ValidationError

from research_agent.config import Settings


def test_embedding_dimension_parses_from_environment_string() -> None:
    settings = Settings(embedding_dimension="1536")  # type: ignore[arg-type]

    assert settings.embedding_dimension == 1536


def test_embedding_dimension_rejects_unsupported_vector_size() -> None:
    with pytest.raises(ValidationError, match="1536"):
        Settings(embedding_dimension="1024")  # type: ignore[arg-type]
