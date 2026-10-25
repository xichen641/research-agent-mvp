import asyncio
import hashlib
from collections.abc import AsyncIterator
from io import BytesIO
from unittest.mock import Mock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.chat.models import ChatMessageModel, ChatSessionModel
from research_agent.discovery.models import ExternalPaperSourceModel
from research_agent.ingestion.models import ChunkDraft, DocumentChunkModel
from research_agent.ingestion.pipeline import IngestionPipeline
from research_agent.main import create_app
from research_agent.notes.models import NoteRevisionModel, PaperNoteModel
from research_agent.papers.domain import CreatedDocument, DocumentStatus, VersionStatus
from research_agent.papers.models import (
    DocumentModel,
    DocumentTaskModel,
    DocumentVersionModel,
)
from research_agent.papers.repository import (
    PaperRecordNotFoundError,
    SqlPaperRepository,
)
from research_agent.papers.routes import get_db_session, get_object_storage
from research_agent.providers.fake import FakeEmbeddingProvider
from research_agent.retrieval.domain import RetrievalQuery, RetrievalScope
from research_agent.retrieval.models import ChunkEmbeddingModel, EmbeddingSpaceModel
from research_agent.retrieval.repository import (
    EmbeddingSpace,
    IncompleteEmbeddingsError,
    SqlEmbeddingRepository,
    SqlHybridRetrievalRepository,
)
from research_agent.shared.errors import AppError
from research_agent.shared.ids import new_id
from research_agent.tasks.domain import TaskStage, TaskStatus
from research_agent.tasks.repository import SqlTaskRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
PDF = b"%PDF-1.7\nrepeatable deletion fixture"


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, *, key, stream, content_type) -> None:
        self.objects[key] = stream.read()

    def get(self, *, key) -> BytesIO:
        return BytesIO(self.objects[key])

    def delete(self, *, key) -> None:
        self.objects.pop(key, None)

    def presigned_get(self, *, key, expires) -> str:
        return "https://example.test/paper.pdf"


def build_client(
    session: AsyncSession | None = None,
    *,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> httpx.AsyncClient:
    app = create_app(web_origin="http://web.test")

    async def sessions() -> AsyncIterator[AsyncSession]:
        if factory is not None:
            async with factory() as independent:
                yield independent
        else:
            assert session is not None
            yield session

    app.dependency_overrides[get_db_session] = sessions
    storage = MemoryStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def seed_paper(
    session: AsyncSession,
    *,
    status: DocumentStatus = DocumentStatus.READY,
    task_status: TaskStatus | None = None,
    sha256: str | None = None,
) -> CreatedDocument:
    created = await SqlPaperRepository(session).create_document_with_version(
        title="Delete fixture",
        sha256=sha256 or new_id().hex * 2,
        filename="delete-fixture.pdf",
        source_object_key="papers/delete-fixture.pdf",
        parser_name="test",
        parser_version="1",
        pipeline_version="pipeline-v1",
    )
    document = await session.get(DocumentModel, created.document_id)
    version = await session.get(DocumentVersionModel, created.version_id)
    task = await session.get(DocumentTaskModel, created.task_id)
    assert document is not None and version is not None and task is not None
    document.status = status
    version.status = (
        VersionStatus.READY if status is DocumentStatus.READY else VersionStatus.FAILED
    )
    task.status = task_status or (
        TaskStatus.READY if status is DocumentStatus.READY else TaskStatus.FAILED
    )
    task.stage = (
        TaskStage.VERIFYING if task.status == TaskStatus.READY else TaskStage.PARSING
    )
    task.progress_current = 8 if task.status == TaskStatus.READY else 2
    task.error_code = (
        "PARSE_PARSER_FAILED" if task.status == TaskStatus.FAILED else None
    )
    await session.commit()
    return created


@pytest.mark.parametrize(
    "status", [DocumentStatus.READY, DocumentStatus.FAILED, DocumentStatus.NEEDS_REVIEW]
)
async def test_delete_terminal_paper_is_idempotent_and_hides_active_api_surfaces(
    db_session: AsyncSession, status: DocumentStatus
) -> None:
    created = await seed_paper(db_session, status=status)
    path = f"/api/v1/documents/{created.document_id}"
    async with build_client(db_session) as client:
        for _ in range(2):
            deleted = await client.delete(path)
            assert deleted.status_code == 200
            assert deleted.json() == {
                "id": str(created.document_id),
                "status": "DELETED",
            }

        assert (await client.get("/api/v1/documents")).json()["items"] == []
        for suffix in ("", "/quality", "/pdf"):
            hidden = await client.get(path + suffix)
            assert hidden.status_code == 404
            assert hidden.json()["code"] == "DOCUMENT_NOT_FOUND"
        assert (await client.get(f"/api/v1/tasks/{created.task_id}")).status_code == 404
        retried = await client.post(f"/api/v1/tasks/{created.task_id}/retry")
        assert retried.status_code == 404
        assert retried.json()["code"] == "TASK_NOT_FOUND"

    document = await db_session.get(DocumentModel, created.document_id)
    assert document is not None
    await db_session.refresh(document)
    assert document.status == DocumentStatus.DELETED
    assert await db_session.get(DocumentVersionModel, created.version_id) is not None
    assert await db_session.get(DocumentTaskModel, created.task_id) is not None


@pytest.mark.parametrize(
    ("document_status", "task_status"),
    [
        (DocumentStatus.PROCESSING, TaskStatus.FAILED),
        (DocumentStatus.DELETING, TaskStatus.READY),
        (DocumentStatus.READY, TaskStatus.RUNNING),
        (DocumentStatus.FAILED, TaskStatus.QUEUED),
    ],
)
async def test_delete_rejects_active_work_without_changing_state(
    db_session: AsyncSession,
    document_status: DocumentStatus,
    task_status: TaskStatus,
) -> None:
    created = await seed_paper(
        db_session, status=document_status, task_status=task_status
    )
    async with build_client(db_session) as client:
        response = await client.delete(f"/api/v1/documents/{created.document_id}")
    assert response.status_code == 409
    assert response.json()["code"] == "DOCUMENT_DELETE_CONFLICT"
    document = await db_session.get(DocumentModel, created.document_id)
    task = await db_session.get(DocumentTaskModel, created.task_id)
    assert document is not None and task is not None
    assert document.status == document_status
    assert task.status == task_status


async def test_delete_checks_tasks_on_older_versions(db_session: AsyncSession) -> None:
    created = await seed_paper(db_session)
    previous_id = new_id()
    db_session.add(
        DocumentVersionModel(
            id=previous_id,
            document_id=created.document_id,
            filename="old.pdf",
            source_object_key="papers/old.pdf",
            parser_name="test",
            parser_version="1",
            pipeline_version="pipeline-v1",
            status=VersionStatus.PROCESSING,
        )
    )
    await db_session.flush()
    db_session.add(
        DocumentTaskModel(
            id=new_id(),
            document_version_id=previous_id,
            stage=TaskStage.PARSING,
            status=TaskStatus.RUNNING,
            progress_current=2,
            progress_total=8,
            retry_count=0,
        )
    )
    await db_session.commit()
    async with build_client(db_session) as client:
        response = await client.delete(f"/api/v1/documents/{created.document_id}")
    assert response.status_code == 409


async def test_missing_delete_and_delete_cors(db_session: AsyncSession) -> None:
    async with build_client(db_session) as client:
        response = await client.delete(f"/api/v1/documents/{new_id()}")
        assert response.status_code == 404
        preflight = await client.options(
            "/api/v1/documents/fixture",
            headers={
                "Origin": "http://web.test",
                "Access-Control-Request-Method": "DELETE",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://web.test"
        assert "DELETE" in preflight.headers["access-control-allow-methods"]


async def test_deleted_pdf_can_be_uploaded_again_and_external_mapping_is_released(
    db_session: AsyncSession,
) -> None:
    created = await seed_paper(db_session, sha256=hashlib.sha256(PDF).hexdigest())
    db_session.add(
        ExternalPaperSourceModel(
            provider="arxiv",
            external_id="1706.03762",
            external_version=1,
            document_id=created.document_id,
            document_version_id=created.version_id,
            task_id=created.task_id,
            metadata_json={},
        )
    )
    await db_session.commit()
    async with build_client(db_session) as client:
        assert (
            await client.delete(f"/api/v1/documents/{created.document_id}")
        ).status_code == 200
        uploaded = await client.post(
            "/api/v1/documents", files={"file": ("again.pdf", PDF, "application/pdf")}
        )
    assert uploaded.status_code == 202
    assert uploaded.json()["duplicate"] is False
    assert uploaded.json()["document_id"] != str(created.document_id)
    assert (
        await db_session.scalar(
            select(func.count()).select_from(ExternalPaperSourceModel)
        )
        == 0
    )
    assert await db_session.scalar(select(func.count()).select_from(DocumentModel)) == 2


async def test_delete_preserves_saved_evidence_but_excludes_both_retrieval_paths(
    db_session: AsyncSession,
) -> None:
    created = await seed_paper(db_session)
    await SqlPaperRepository(db_session).replace_chunks(
        created.version_id,
        "pipeline-v1",
        [ChunkDraft(0, ("Methods",), "occlusion evidence", "text", 1, 1, ("e1",), 2)],
    )
    chunk = await db_session.scalar(select(DocumentChunkModel))
    assert chunk is not None
    vector = [1.0] + [0.0] * 1535
    space_id, note_id, revision_id, session_id, message_id = [
        new_id() for _ in range(5)
    ]
    db_session.add_all(
        [
            EmbeddingSpaceModel(
                id=space_id,
                model_id="test",
                dimension=1536,
                pipeline_version="pipeline-v1",
                active=True,
            ),
            PaperNoteModel(id=note_id, document_id=created.document_id),
            ChatSessionModel(
                id=session_id,
                title="Saved chat",
                scope={"document_ids": [str(created.document_id)]},
            ),
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            ChunkEmbeddingModel(
                chunk_id=chunk.id, embedding_space_id=space_id, embedding=vector
            ),
            NoteRevisionModel(
                id=revision_id,
                note_id=note_id,
                revision_number=1,
                source="USER_EDIT",
                content={"saved": "evidence note"},
            ),
            ChatMessageModel(
                id=message_id,
                session_id=session_id,
                role="assistant",
                sequence=1,
                content={"saved": "answer", "chunk_id": str(chunk.id)},
            ),
        ]
    )
    await db_session.commit()
    repository = SqlHybridRetrievalRepository(db_session)
    query = RetrievalQuery("occlusion", ("occlusion",), "occlusion", RetrievalScope())
    assert len(await repository.lexical_search(query, limit=5)) == 1
    assert len(await repository.vector_search(query, vector, limit=5)) == 1

    async with build_client(db_session) as client:
        assert (
            await client.delete(f"/api/v1/documents/{created.document_id}")
        ).status_code == 200

    for scope in (
        RetrievalScope(),
        RetrievalScope(
            document_version_ids=(created.version_id,), restrict_to_scope=True
        ),
    ):
        query = RetrievalQuery("occlusion", ("occlusion",), "occlusion", scope)
        assert await repository.lexical_search(query, limit=5) == []
        assert await repository.vector_search(query, vector, limit=5) == []
    assert (
        await db_session.get(DocumentChunkModel, chunk.id)
    ).content == "occlusion evidence"
    assert await db_session.get(ChunkEmbeddingModel, (chunk.id, space_id)) is not None
    assert (await db_session.get(NoteRevisionModel, revision_id)).content == {
        "saved": "evidence note"
    }
    assert (await db_session.get(ChatMessageModel, message_id)).content[
        "saved"
    ] == "answer"


async def test_concurrent_retry_and_delete_cannot_revive_deleted_document(
    independent_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with independent_session_factory() as session:
        created = await seed_paper(session, status=DocumentStatus.FAILED)
    async with build_client(factory=independent_session_factory) as client:
        deleted, retried = await asyncio.gather(
            client.delete(f"/api/v1/documents/{created.document_id}"),
            client.post(f"/api/v1/tasks/{created.task_id}/retry"),
        )
    assert (deleted.status_code, retried.status_code) in {(200, 404), (409, 202)}
    async with independent_session_factory() as session:
        document = await session.get(DocumentModel, created.document_id)
        task = await session.get(DocumentTaskModel, created.task_id)
        assert document is not None and task is not None
        if deleted.status_code == 200:
            assert document.status == DocumentStatus.DELETED
            assert task.status == TaskStatus.FAILED
        else:
            assert document.status == DocumentStatus.FAILED
            assert task.status == TaskStatus.QUEUED


@pytest_asyncio.fixture
async def worker_activation_state(
    independent_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[CreatedDocument, EmbeddingSpace]]:
    async with independent_session_factory() as setup:
        created = await seed_paper(setup)
        embeddings = SqlEmbeddingRepository(setup)
        space = await embeddings.get_or_create_space(
            model_id=f"deleted-write-guard-{created.document_id}",
            dimension=1536,
            pipeline_version="pipeline-v1",
        )
        await setup.commit()
    try:
        yield created, space
    finally:
        async with independent_session_factory() as cleanup:
            await cleanup.execute(
                delete(EmbeddingSpaceModel).where(EmbeddingSpaceModel.id == space.id)
            )
            await cleanup.commit()


@pytest.mark.parametrize("operation", ["mark_failed", "persist", "switch", "activate"])
async def test_stale_worker_final_writes_cannot_revive_deleted_document(
    independent_session_factory: async_sessionmaker[AsyncSession],
    worker_activation_state: tuple[CreatedDocument, EmbeddingSpace],
    operation: str,
) -> None:
    created, space = worker_activation_state

    async with independent_session_factory() as worker_session:
        cached = await worker_session.get(DocumentModel, created.document_id)
        version = await worker_session.get(DocumentVersionModel, created.version_id)
        assert cached is not None and version is not None
        assert cached.status == DocumentStatus.READY
        async with build_client(factory=independent_session_factory) as client:
            response = await client.delete(f"/api/v1/documents/{created.document_id}")
            assert response.status_code == 200
        # Simulate a worker that loaded its ORM objects before deletion committed.
        assert cached.status == DocumentStatus.READY
        papers = SqlPaperRepository(worker_session)
        if operation == "activate":
            with pytest.raises(IncompleteEmbeddingsError):
                await SqlEmbeddingRepository(worker_session).activate_if_complete(
                    version_id=created.version_id,
                    task_id=created.task_id,
                    pipeline_version="pipeline-v1",
                    embedding_space_id=space.id,
                )
        else:
            with pytest.raises(PaperRecordNotFoundError):
                if operation == "mark_failed":
                    await papers.mark_parse_failed(created.version_id)
                elif operation == "switch":
                    await papers.switch_current_version(
                        created.document_id, created.version_id, DocumentStatus.READY
                    )
                else:
                    await papers.persist_parse_artifacts(
                        created.version_id,
                        parser_name="late-parser",
                        parser_version="2",
                        json_object_key="late.json",
                        markdown_object_key="late.md",
                        image_object_keys=(),
                        parse_quality={"late": True},
                        version_status=VersionStatus.READY,
                        document_status=DocumentStatus.READY,
                    )
        await worker_session.commit()
        await worker_session.refresh(cached)
        await worker_session.refresh(version)
        assert cached.status == DocumentStatus.DELETED
        assert version.status == VersionStatus.READY
        assert version.parser_name == "test"
        assert version.parsed_json_object_key is None
        embedding_space = await worker_session.get(EmbeddingSpaceModel, space.id)
        assert embedding_space is not None and embedding_space.active is False


async def test_deleted_paper_stops_worker_before_parser_or_provider_calls(
    db_session: AsyncSession,
) -> None:
    created = await seed_paper(db_session, status=DocumentStatus.FAILED)
    papers = SqlPaperRepository(db_session)
    assert await papers.delete_document(created.document_id)
    await papers.commit()
    parser = Mock()
    provider = FakeEmbeddingProvider(dimension=1536)
    pipeline = IngestionPipeline(
        parser,
        MemoryStorage(),
        papers,
        SqlTaskRepository(db_session),
        embedding_provider=provider,
        embedding_repository=SqlEmbeddingRepository(db_session),
    )
    with patch.object(provider, "embed_documents") as embed:
        with pytest.raises(AppError) as caught:
            await pipeline.run(created.version_id)
        assert caught.value.code == "PARSE_RECORD_NOT_FOUND"
        assert caught.value.retryable is False
        parser.parse.assert_not_called()
        embed.assert_not_called()
