from uuid import uuid4

import pytest
from pydantic import ValidationError

from research_agent.chat.citations import CitationVerifier
from research_agent.chat.domain import (
    AnswerDraft,
    CitationDraft,
    ModelKnowledgeBlock,
    PaperEvidenceBlock,
)
from research_agent.retrieval.domain import RetrievalCandidate


def candidate(content: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        title="Paper",
        section_path=("Methods",),
        content=content,
        content_type="text",
        page_start=2,
        page_end=2,
    )


def test_verified_answer_keeps_labels_and_only_valid_paper_citations() -> None:
    source = candidate("The method uses visible regions.")
    draft = AnswerDraft(
        blocks=[
            PaperEvidenceBlock(
                text="The method uses visible regions.",
                citations=[
                    CitationDraft(
                        chunk_id=source.chunk_id,
                        quote="The method uses visible regions.",
                    ),
                    CitationDraft(chunk_id=source.chunk_id, quote="invented quote"),
                ],
            ),
            ModelKnowledgeBlock(text="General vision background."),
        ]
    )

    result = CitationVerifier().verify_answer(draft, [source])

    assert result.failed_block_indexes == []
    assert [block.type for block in result.answer.blocks] == [
        "paper_evidence",
        "model_knowledge",
    ]
    paper_block = result.answer.blocks[0]
    assert paper_block.type == "paper_evidence"
    assert len(paper_block.citations) == 1
    assert paper_block.citations[0].quote == source.content


def test_paper_block_without_valid_citation_fails() -> None:
    source = candidate("Supported text.")
    draft = AnswerDraft(
        blocks=[
            PaperEvidenceBlock(
                text="Unsupported claim.",
                citations=[
                    CitationDraft(chunk_id=source.chunk_id, quote="invented quote")
                ],
            )
        ]
    )

    result = CitationVerifier().verify_answer(draft, [source])

    assert result.answer.blocks == []
    assert result.failed_block_indexes == [0]


def test_model_knowledge_cannot_carry_citations() -> None:
    with pytest.raises(ValidationError):
        ModelKnowledgeBlock.model_validate(
            {
                "type": "model_knowledge",
                "text": "Background",
                "citations": [],
            }
        )
