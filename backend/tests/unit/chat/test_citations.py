from collections.abc import Callable
from uuid import UUID, uuid4

import pytest

from research_agent.chat.citations import CitationVerifier
from research_agent.chat.domain import CitationDraft
from research_agent.retrieval.domain import RetrievalCandidate


@pytest.fixture
def candidate_factory() -> Callable[..., RetrievalCandidate]:
    def build(
        *,
        content: str = "The method uses visible regions.",
        page_start: int = 4,
        page_end: int = 4,
        document_version_id: UUID | None = None,
    ) -> RetrievalCandidate:
        return RetrievalCandidate(
            chunk_id=uuid4(),
            document_id=uuid4(),
            document_version_id=document_version_id or uuid4(),
            title="Occluded ReID",
            section_path=("Methods",),
            content=content,
            content_type="text",
            page_start=page_start,
            page_end=page_end,
        )

    return build


def test_quote_must_exist_in_retrieved_chunk(candidate_factory) -> None:
    candidate = candidate_factory(
        content="The method uses visible regions.", page_start=4
    )
    verifier = CitationVerifier()

    result = verifier.verify(
        citations=[
            CitationDraft(
                chunk_id=candidate.chunk_id,
                quote="a sentence that is absent",
            )
        ],
        candidates=[candidate],
    )

    assert result.valid == []
    assert result.invalid[0].reason == "QUOTE_NOT_FOUND"


def test_normalized_verbatim_quote_produces_source_metadata(
    candidate_factory,
) -> None:
    candidate = candidate_factory(content="Visible\n  Ａ regions are matched.")

    result = CitationVerifier().verify(
        citations=[
            CitationDraft(
                chunk_id=candidate.chunk_id,
                document_version_id=candidate.document_version_id,
                quote="Visible A regions are matched.",
            )
        ],
        candidates=[candidate],
    )

    assert result.invalid == []
    assert result.valid[0].document_id == candidate.document_id
    assert result.valid[0].page_start == candidate.page_start


def test_citation_must_belong_to_final_retrieved_candidates(
    candidate_factory,
) -> None:
    candidate = candidate_factory()

    result = CitationVerifier().verify(
        citations=[CitationDraft(chunk_id=uuid4(), quote=candidate.content)],
        candidates=[candidate],
    )

    assert result.valid == []
    assert result.invalid[0].reason == "CHUNK_NOT_RETRIEVED"


def test_stale_document_version_is_rejected(candidate_factory) -> None:
    candidate = candidate_factory()

    result = CitationVerifier().verify(
        citations=[
            CitationDraft(
                chunk_id=candidate.chunk_id,
                document_version_id=uuid4(),
                quote=candidate.content,
            )
        ],
        candidates=[candidate],
    )

    assert result.valid == []
    assert result.invalid[0].reason == "STALE_DOCUMENT_VERSION"


def test_candidate_requires_positive_ordered_page_range(candidate_factory) -> None:
    candidate = candidate_factory(page_start=0, page_end=0)

    result = CitationVerifier().verify(
        citations=[
            CitationDraft(chunk_id=candidate.chunk_id, quote=candidate.content)
        ],
        candidates=[candidate],
    )

    assert result.valid == []
    assert result.invalid[0].reason == "PAGE_UNAVAILABLE"
