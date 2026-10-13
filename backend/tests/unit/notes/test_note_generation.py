from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from research_agent.notes.domain import (
    NOTE_TEMPLATE,
    NoteEvidence,
    NoteField,
    NoteRevisionRecord,
    PaperNoteContent,
)
from research_agent.notes.service import NoteService
from research_agent.providers.protocols import ChatTurn
from research_agent.retrieval.domain import RetrievalQuery
from research_agent.retrieval.domain import RetrievalCandidate
from research_agent.shared.errors import AppError


class FakeLLM:
    def __init__(self, response: PaperNoteContent) -> None:
        self.response = response

    async def generate_structured(
        self,
        *,
        operation: str,
        messages: list[ChatTurn],
        output_type: type[PaperNoteContent],
    ) -> PaperNoteContent:
        del messages, output_type
        assert operation == "paper_note"
        return self.response


class FakeRetrieval:
    def __init__(self, candidate: RetrievalCandidate) -> None:
        self.candidate = candidate

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        lexical_limit: int = 40,
        vector_limit: int = 40,
        fused_limit: int = 25,
        final_limit: int = 10,
    ) -> list[RetrievalCandidate]:
        del query, lexical_limit, vector_limit, fused_limit, final_limit
        return [self.candidate]


class FakeRepository:
    def __init__(self) -> None:
        self.revisions: list[NoteRevisionRecord] = []
        self.current_revision_id: UUID | None = None

    async def create_ai_draft(
        self,
        *,
        document_id: UUID,
        content: PaperNoteContent,
        evidence: list[NoteEvidence],
        activate_if_new: bool,
    ) -> NoteRevisionRecord:
        del document_id, evidence
        revision = NoteRevisionRecord(
            id=uuid4(),
            note_id=uuid4(),
            revision_number=len(self.revisions) + 1,
            source="AI_DRAFT",
            content=content,
            created_at=datetime.now(UTC),
            evidence=(),
        )
        self.revisions.append(revision)
        if activate_if_new and self.current_revision_id is None:
            self.current_revision_id = revision.id
        return revision


def generated_content(chunk_id: UUID) -> PaperNoteContent:
    return PaperNoteContent(
        fields=[
            NoteField(
                key=key,
                title=title,
                markdown=("" if key == "user_comments" else f"Evidence for {key}"),
                evidence_chunk_ids=([] if key == "user_comments" else [chunk_id]),
                status=("USER_CONFIRMED" if key == "user_comments" else "AI_GENERATED"),
            )
            for key, title in NOTE_TEMPLATE
        ]
    )


@pytest.mark.asyncio
async def test_generation_is_explicit_and_evidence_backed() -> None:
    document_id = uuid4()
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=document_id,
        document_version_id=uuid4(),
        title="Paper",
        section_path=("Method",),
        content="Uses visible-region matching.",
        content_type="text",
        page_start=2,
        page_end=2,
    )
    repository = FakeRepository()
    service = NoteService(
        FakeLLM(generated_content(candidate.chunk_id)),
        FakeRetrieval(candidate),
        repository,
    )

    revision = await service.generate_draft(document_id, activate_if_new=True)

    assert revision.content.fields[3].status == "AI_GENERATED"
    assert revision.content.fields[3].evidence_chunk_ids == [candidate.chunk_id]
    assert revision.content.field("user_comments").markdown == ""
    assert repository.current_revision_id == revision.id


@pytest.mark.asyncio
async def test_generation_rejects_an_ai_field_without_verified_evidence() -> None:
    document_id = uuid4()
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=document_id,
        document_version_id=uuid4(),
        title="Paper",
        section_path=("Method",),
        content="Evidence",
        content_type="text",
        page_start=1,
        page_end=1,
    )
    content = generated_content(candidate.chunk_id)
    content.field("innovations").evidence_chunk_ids = []
    service = NoteService(FakeLLM(content), FakeRetrieval(candidate), FakeRepository())

    with pytest.raises(AppError, match="verified evidence") as captured:
        await service.generate_draft(document_id, activate_if_new=True)

    assert captured.value.code == "NOTE_EVIDENCE_INVALID"


@pytest.mark.asyncio
async def test_regeneration_never_replaces_the_current_revision() -> None:
    document_id = uuid4()
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=document_id,
        document_version_id=uuid4(),
        title="Paper",
        section_path=("Results",),
        content="The measured result is 91 percent.",
        content_type="text",
        page_start=4,
        page_end=4,
    )
    repository = FakeRepository()
    repository.current_revision_id = uuid4()
    original = repository.current_revision_id
    service = NoteService(
        FakeLLM(generated_content(candidate.chunk_id)),
        FakeRetrieval(candidate),
        repository,
    )

    await service.generate_draft(document_id, activate_if_new=False)

    assert repository.current_revision_id == original


@pytest.mark.asyncio
async def test_generation_rejects_model_authored_user_comments() -> None:
    document_id = uuid4()
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=document_id,
        document_version_id=uuid4(),
        title="Paper",
        section_path=("Discussion",),
        content="A limitation is reported.",
        content_type="text",
        page_start=6,
        page_end=6,
    )
    generated = generated_content(candidate.chunk_id)
    generated.field("user_comments").markdown = "The model should not write this."
    service = NoteService(
        FakeLLM(generated), FakeRetrieval(candidate), FakeRepository()
    )

    with pytest.raises(AppError) as captured:
        await service.generate_draft(document_id, activate_if_new=True)

    assert captured.value.code == "NOTE_USER_COMMENTS_GENERATED"
