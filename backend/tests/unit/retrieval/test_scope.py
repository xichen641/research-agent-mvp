from uuid import uuid4

import pytest

from research_agent.retrieval.domain import RetrievalQuery, RetrievalScope


def test_scope_rejects_duplicate_document_ids() -> None:
    document_id = uuid4()

    with pytest.raises(ValueError, match="duplicates"):
        RetrievalScope(document_ids=(document_id, document_id))


def test_query_rejects_blank_or_missing_search_text() -> None:
    scope = RetrievalScope()

    with pytest.raises(ValueError, match="original"):
        RetrievalQuery(" ", ("term",), "semantic", scope)
    with pytest.raises(ValueError, match="lexical"):
        RetrievalQuery("question", (), "semantic", scope)
    with pytest.raises(ValueError, match="semantic"):
        RetrievalQuery("question", ("term",), " ", scope)
