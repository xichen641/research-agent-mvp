import json
from typing import Protocol
from uuid import UUID

from research_agent.chat.citations import CitationVerifier
from research_agent.chat.domain import CitationDraft
from research_agent.notes.domain import (
    NOTE_TEMPLATE,
    NoteEvidence,
    NoteRevisionRecord,
    PaperNoteContent,
)
from research_agent.providers.protocols import ChatTurn
from research_agent.retrieval.domain import (
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalScope,
)
from research_agent.shared.errors import AppError


class NoteRetrieval(Protocol):
    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        lexical_limit: int = 40,
        vector_limit: int = 40,
        fused_limit: int = 25,
        final_limit: int = 10,
    ) -> list[RetrievalCandidate]: ...


class NoteLLM(Protocol):
    async def generate_structured(
        self,
        *,
        operation: str,
        messages: list[ChatTurn],
        output_type: type[PaperNoteContent],
    ) -> PaperNoteContent: ...


class NoteRepository(Protocol):
    async def create_ai_draft(
        self,
        *,
        document_id: UUID,
        content: PaperNoteContent,
        evidence: list[NoteEvidence],
        activate_if_new: bool,
    ) -> NoteRevisionRecord: ...


class NoteService:
    """Generate a note only after an explicit command and verify every AI field.

    仅在显式命令后生成笔记，并逐字段校验所有 AI 内容的论文证据。
    """

    def __init__(
        self,
        llm: NoteLLM,
        retrieval: NoteRetrieval,
        repository: NoteRepository,
        *,
        verifier: CitationVerifier | None = None,
    ) -> None:
        self._llm = llm
        self._retrieval = retrieval
        self._repository = repository
        self._verifier = verifier or CitationVerifier()

    async def generate_draft(
        self, document_id: UUID, *, activate_if_new: bool
    ) -> NoteRevisionRecord:
        query = RetrievalQuery(
            original="Summarize the paper method, evidence, results, and limitations",
            lexical_queries=(
                "method results limitations datasets experiments ablation",
                "方法 结果 局限 数据集 实验 消融",
            ),
            semantic_query=(
                "Research question, method, innovations, datasets, experimental "
                "results, ablations, limitations, related work and implications"
            ),
            scope=RetrievalScope(document_ids=(document_id,)),
        )
        candidates = await self._retrieval.retrieve(
            query,
            lexical_limit=80,
            vector_limit=80,
            fused_limit=50,
            final_limit=30,
        )
        if not candidates:
            raise AppError(
                "NOTE_EVIDENCE_INSUFFICIENT",
                "The paper does not contain enough retrievable evidence",
            )
        content = await self._llm.generate_structured(
            operation="paper_note",
            messages=[
                ChatTurn(
                    role="system",
                    content=(
                        "Create every field in the fixed order. Every field except "
                        "user_comments must use status AI_GENERATED and cite at least "
                        "one supplied chunk id. user_comments must be empty, have no "
                        "evidence, and use status USER_CONFIRMED. Do not use facts "
                        "outside the supplied paper evidence."
                    ),
                ),
                ChatTurn(
                    role="user",
                    content=json.dumps(
                        {
                            "field_template": [
                                {"key": key, "title": title}
                                for key, title in NOTE_TEMPLATE
                            ],
                            "evidence": [
                                {
                                    "chunk_id": str(candidate.chunk_id),
                                    "section_path": candidate.section_path,
                                    "page_start": candidate.page_start,
                                    "page_end": candidate.page_end,
                                    "content": candidate.content,
                                }
                                for candidate in candidates
                            ],
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
            output_type=PaperNoteContent,
        )
        evidence = self._verify_content(content, candidates, document_id)
        return await self._repository.create_ai_draft(
            document_id=document_id,
            content=content,
            evidence=evidence,
            activate_if_new=activate_if_new,
        )

    def _verify_content(
        self,
        content: PaperNoteContent,
        candidates: list[RetrievalCandidate],
        document_id: UUID,
    ) -> list[NoteEvidence]:
        candidates_by_id = {candidate.chunk_id: candidate for candidate in candidates}
        verified: list[NoteEvidence] = []
        comments = content.field("user_comments")
        if (
            comments.markdown.strip()
            or comments.evidence_chunk_ids
            or comments.status != "USER_CONFIRMED"
        ):
            raise AppError(
                "NOTE_USER_COMMENTS_GENERATED",
                "The language model must not generate user comments",
            )
        for field in content.fields:
            if field.key == "user_comments":
                continue
            if field.status != "AI_GENERATED" or not field.evidence_chunk_ids:
                raise _invalid_evidence()
            for chunk_id in dict.fromkeys(field.evidence_chunk_ids):
                candidate = candidates_by_id.get(chunk_id)
                if candidate is None or candidate.document_id != document_id:
                    raise _invalid_evidence()
                quote = candidate.content[:1000]
                result = self._verifier.verify(
                    citations=[
                        CitationDraft(
                            chunk_id=chunk_id,
                            document_version_id=candidate.document_version_id,
                            quote=quote,
                        )
                    ],
                    candidates=candidates,
                )
                if len(result.valid) != 1 or result.invalid:
                    raise _invalid_evidence()
                citation = result.valid[0]
                verified.append(
                    NoteEvidence(
                        field_key=field.key,
                        chunk_id=citation.chunk_id,
                        document_id=citation.document_id,
                        document_version_id=citation.document_version_id,
                        title=citation.title,
                        section_path=citation.section_path,
                        page_start=citation.page_start,
                        page_end=citation.page_end,
                        quote=citation.quote,
                    )
                )
        return verified


def _invalid_evidence() -> AppError:
    return AppError(
        "NOTE_EVIDENCE_INVALID",
        "Every AI-generated note field requires verified evidence",
    )
