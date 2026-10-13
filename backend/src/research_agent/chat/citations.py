from dataclasses import dataclass
from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict

from research_agent.chat.domain import (
    AnswerDraft,
    CitationDraft,
    ModelKnowledgeBlock,
    VerifiedAnswer,
    VerifiedCitation,
    VerifiedPaperEvidenceBlock,
)
from research_agent.retrieval.domain import RetrievalCandidate


CitationFailureReason = Literal[
    "CHUNK_NOT_RETRIEVED",
    "STALE_DOCUMENT_VERSION",
    "QUOTE_NOT_FOUND",
    "PAGE_UNAVAILABLE",
]


class InvalidCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation: CitationDraft
    reason: CitationFailureReason


class CitationVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: list[VerifiedCitation]
    invalid: list[InvalidCitation]


@dataclass(frozen=True, slots=True)
class AnswerVerificationResult:
    answer: VerifiedAnswer
    invalid: tuple[InvalidCitation, ...]
    failed_block_indexes: list[int]


class CitationVerifier:
    def verify(
        self,
        *,
        citations: list[CitationDraft],
        candidates: list[RetrievalCandidate],
    ) -> CitationVerificationResult:
        candidate_by_id = {candidate.chunk_id: candidate for candidate in candidates}
        valid: list[VerifiedCitation] = []
        invalid: list[InvalidCitation] = []
        seen: set[tuple[object, ...]] = set()

        for citation in citations:
            candidate = candidate_by_id.get(citation.chunk_id)
            reason = _failure_reason(citation, candidate)
            if reason is not None:
                invalid.append(InvalidCitation(citation=citation, reason=reason))
                continue
            assert candidate is not None
            identity = (
                citation.chunk_id,
                candidate.document_version_id,
                normalize_quote(citation.quote),
            )
            if identity in seen:
                continue
            seen.add(identity)
            valid.append(
                VerifiedCitation(
                    chunk_id=candidate.chunk_id,
                    document_id=candidate.document_id,
                    document_version_id=candidate.document_version_id,
                    title=candidate.title,
                    section_path=candidate.section_path,
                    page_start=candidate.page_start,
                    page_end=candidate.page_end,
                    quote=citation.quote,
                )
            )

        return CitationVerificationResult(valid=valid, invalid=invalid)

    def verify_answer(
        self,
        draft: AnswerDraft,
        candidates: list[RetrievalCandidate],
    ) -> AnswerVerificationResult:
        blocks: list[VerifiedPaperEvidenceBlock | ModelKnowledgeBlock] = []
        invalid: list[InvalidCitation] = []
        failed_block_indexes: list[int] = []

        for index, block in enumerate(draft.blocks):
            if block.type == "model_knowledge":
                blocks.append(block)
                continue
            result = self.verify(citations=block.citations, candidates=candidates)
            invalid.extend(result.invalid)
            if not result.valid:
                failed_block_indexes.append(index)
                continue
            blocks.append(
                VerifiedPaperEvidenceBlock(
                    text=block.text,
                    citations=result.valid,
                )
            )

        return AnswerVerificationResult(
            answer=VerifiedAnswer(blocks=blocks),
            invalid=tuple(invalid),
            failed_block_indexes=failed_block_indexes,
        )


def normalize_quote(value: str) -> str:
    """Normalize representation and whitespace without paraphrasing text."""

    return " ".join(unicodedata.normalize("NFKC", value).split())


def _failure_reason(
    citation: CitationDraft,
    candidate: RetrievalCandidate | None,
) -> CitationFailureReason | None:
    if candidate is None:
        return "CHUNK_NOT_RETRIEVED"
    if (
        citation.document_version_id is not None
        and citation.document_version_id != candidate.document_version_id
    ):
        return "STALE_DOCUMENT_VERSION"
    if (
        candidate.page_start <= 0
        or candidate.page_end <= 0
        or candidate.page_end < candidate.page_start
    ):
        return "PAGE_UNAVAILABLE"
    if normalize_quote(citation.quote) not in normalize_quote(candidate.content):
        return "QUOTE_NOT_FOUND"
    return None
