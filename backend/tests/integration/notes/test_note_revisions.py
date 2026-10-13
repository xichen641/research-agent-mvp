from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from research_agent.ingestion.models import DocumentChunkModel
from research_agent.notes.domain import (
    NOTE_TEMPLATE,
    NoteEvidence,
    NoteField,
    PaperNoteContent,
)
from research_agent.notes.repository import SqlNoteRepository
from research_agent.notes.models import PaperNoteModel
from research_agent.papers.models import DocumentModel, DocumentVersionModel


def note_content(
    chunk_id: UUID,
    *,
    confirmed: bool = False,
    comments: str = "",
) -> PaperNoteContent:
    return PaperNoteContent(
        fields=[
            NoteField(
                key=key,
                title=title,
                markdown=(comments if key == "user_comments" else f"{title} content"),
                evidence_chunk_ids=([] if key == "user_comments" else [chunk_id]),
                status=(
                    "USER_CONFIRMED"
                    if confirmed or key == "user_comments"
                    else "AI_GENERATED"
                ),
            )
            for key, title in NOTE_TEMPLATE
        ]
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_note_revisions_survive_restart_and_regeneration_is_non_destructive(
    migrated_database_url: str,
) -> None:
    document_id = uuid4()
    version_id = uuid4()
    chunk_id = uuid4()
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    evidence = NoteEvidence(
        field_key="innovations",
        chunk_id=chunk_id,
        document_id=document_id,
        document_version_id=version_id,
        title="Versioned note paper",
        section_path=("Method",),
        page_start=3,
        page_end=3,
        quote="The method uses region-level evidence.",
    )

    async with factory() as session:
        document = DocumentModel(
            id=document_id,
            title="Versioned note paper",
            sha256=uuid4().hex + uuid4().hex,
            status="READY",
            current_version_id=None,
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentVersionModel(
                id=version_id,
                document_id=document_id,
                filename="paper.pdf",
                source_object_key="papers/test.pdf",
                parser_name="docling",
                parser_version="1",
                pipeline_version="1",
                status="READY",
            )
        )
        await session.flush()
        document.current_version_id = version_id
        session.add(
            DocumentChunkModel(
                id=chunk_id,
                document_version_id=version_id,
                chunk_index=0,
                section_path=["Method"],
                content=evidence.quote,
                lexical_text=evidence.quote,
                content_type="text",
                page_start=3,
                page_end=3,
                element_ids=["e1"],
                token_count=8,
                pipeline_version="1",
            )
        )
        await session.commit()

        repository = SqlNoteRepository(session)
        assert await repository.get_by_document(document_id) is None
        initial = await repository.create_ai_draft(
            document_id=document_id,
            content=note_content(chunk_id),
            evidence=[
                replace(evidence, field_key=key)
                for key, _ in NOTE_TEMPLATE
                if key != "user_comments"
            ],
            activate_if_new=True,
        )
        user = await repository.save_user_edit(
            note_id=initial.note_id,
            content=note_content(chunk_id, confirmed=True, comments="Keep this edit"),
        )
        draft = await repository.create_ai_draft(
            document_id=document_id,
            content=note_content(chunk_id),
            evidence=[evidence],
            activate_if_new=False,
        )

    # A fresh engine/session simulates a service restart and proves durable state.
    # 新建引擎和会话模拟服务重启，用于证明版本与证据均已持久化。
    await engine.dispose()
    restarted_engine = create_async_engine(migrated_database_url)
    restarted_factory = async_sessionmaker(restarted_engine, expire_on_commit=False)
    async with restarted_factory() as session:
        repository = SqlNoteRepository(session)
        persisted = await repository.get_by_document(document_id)
        revisions = await repository.list_revisions(initial.note_id)
        assert persisted is not None
        assert persisted.current_revision_id == user.id
        assert persisted.current_revision is not None
        assert persisted.current_revision.content.field("user_comments").markdown == (
            "Keep this edit"
        )
        assert draft.id != persisted.current_revision_id
        assert [revision.source for revision in revisions] == [
            "AI_DRAFT",
            "USER_EDIT",
            "AI_DRAFT",
        ]
        assert revisions[-1].evidence[0].page_start == 3
        await session.execute(
            delete(PaperNoteModel).where(PaperNoteModel.document_id == document_id)
        )
        await session.execute(
            delete(DocumentModel).where(DocumentModel.id == document_id)
        )
        await session.commit()
    await restarted_engine.dispose()
