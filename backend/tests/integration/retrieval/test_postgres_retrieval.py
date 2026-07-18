from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.ingestion.chunker import lexical_text
from research_agent.ingestion.models import ChunkDraft, DocumentChunkModel
from research_agent.papers.domain import DocumentStatus, VersionStatus
from research_agent.papers.models import DocumentModel, DocumentVersionModel
from research_agent.papers.repository import SqlPaperRepository
from research_agent.providers.fake import FakeEmbeddingProvider, FakeRerankerProvider
from research_agent.retrieval.domain import RetrievalQuery, RetrievalScope
from research_agent.retrieval.models import ChunkEmbeddingModel, EmbeddingSpaceModel
from research_agent.retrieval.repository import SqlHybridRetrievalRepository
from research_agent.retrieval.service import RetrievalService


def vector(first: float, second: float = 0.0) -> list[float]:
    return [first, second, *([0.0] * 1534)]


async def create_paper(
    session: AsyncSession,
    *,
    title: str,
    sha: str,
    status: DocumentStatus,
) -> tuple[UUID, UUID]:
    papers = SqlPaperRepository(session)
    created = await papers.create_document_with_version(
        title=title,
        sha256=sha,
        filename=f"{title}.pdf",
        source_object_key=f"papers/{sha}.pdf",
        parser_name="test",
        parser_version="1",
        pipeline_version="pipeline-v1",
    )
    document = await session.get(DocumentModel, created.document_id)
    version = await session.get(DocumentVersionModel, created.version_id)
    assert document is not None and version is not None
    document.status = status
    version.status = (
        VersionStatus.READY
        if status is DocumentStatus.READY
        else VersionStatus.PROCESSING
    )
    return created.document_id, created.version_id


async def add_chunk(
    session: AsyncSession,
    *,
    version_id: UUID,
    index: int,
    content: str,
    content_type: str = "text",
) -> UUID:
    papers = SqlPaperRepository(session)
    await papers.replace_chunks(
        version_id,
        "pipeline-v1",
        [
            ChunkDraft(
                chunk_index=index,
                section_path=("Methods",),
                content=content,
                content_type=content_type,
                page_start=1,
                page_end=2,
                element_ids=(f"element-{index}",),
                token_count=len(content.split()),
            )
        ],
    )
    await session.flush()
    chunk = await session.scalar(
        select(DocumentChunkModel).where(
            DocumentChunkModel.document_version_id == version_id
        )
    )
    assert chunk is not None
    return chunk.id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_retrieval_filters_scope_ready_current_and_active_space(
    db_session: AsyncSession,
) -> None:
    scoped_document, scoped_version = await create_paper(
        db_session,
        title="Scoped ready",
        sha="1" * 64,
        status=DocumentStatus.READY,
    )
    other_document, other_version = await create_paper(
        db_session,
        title="Other ready",
        sha="2" * 64,
        status=DocumentStatus.READY,
    )
    nonready_document, nonready_version = await create_paper(
        db_session,
        title="Not ready",
        sha="3" * 64,
        status=DocumentStatus.PROCESSING,
    )
    scoped_chunk = await add_chunk(
        db_session,
        version_id=scoped_version,
        index=0,
        content="occlusion robust matching",
    )
    reference_chunk = uuid4()
    db_session.add(
        DocumentChunkModel(
            id=reference_chunk,
            document_version_id=scoped_version,
            chunk_index=1,
            section_path=["References"],
            content="occlusion 遮挡 cited work",
            lexical_text=lexical_text("occlusion 遮挡 cited work"),
            content_type="reference",
            page_start=3,
            page_end=3,
            element_ids=["reference-1"],
            token_count=4,
            pipeline_version="pipeline-v1",
        )
    )
    chinese_chunk = uuid4()
    chinese_content = "该方法能够处理遮挡问题"
    db_session.add(
        DocumentChunkModel(
            id=chinese_chunk,
            document_version_id=scoped_version,
            chunk_index=2,
            section_path=["方法"],
            content=chinese_content,
            lexical_text=lexical_text(chinese_content),
            content_type="table",
            page_start=2,
            page_end=2,
            element_ids=["chinese-1"],
            token_count=1,
            pipeline_version="pipeline-v1",
        )
    )
    other_chunk = await add_chunk(
        db_session,
        version_id=other_version,
        index=0,
        content="occlusion 遮挡 from another paper",
    )
    nonready_chunk = await add_chunk(
        db_session,
        version_id=nonready_version,
        index=0,
        content="occlusion 遮挡 unfinished",
    )

    historical_version = uuid4()
    db_session.add(
        DocumentVersionModel(
            id=historical_version,
            document_id=scoped_document,
            filename="historical.pdf",
            source_object_key="papers/historical.pdf",
            parser_name="test",
            parser_version="1",
            pipeline_version="pipeline-v1",
            status=VersionStatus.READY,
        )
    )
    await db_session.flush()
    historical_chunk = await add_chunk(
        db_session,
        version_id=historical_version,
        index=0,
        content="occlusion 遮挡 historical",
    )

    active_space = EmbeddingSpaceModel(
        id=uuid4(),
        model_id="active-model",
        dimension=1536,
        pipeline_version="pipeline-v1",
        active=True,
    )
    inactive_space = EmbeddingSpaceModel(
        id=uuid4(),
        model_id="inactive-model",
        dimension=1536,
        pipeline_version="pipeline-v1",
        active=False,
    )
    db_session.add_all([active_space, inactive_space])
    await db_session.flush()
    db_session.add_all(
        [
            ChunkEmbeddingModel(
                chunk_id=scoped_chunk,
                embedding_space_id=active_space.id,
                embedding=vector(1.0),
            ),
            ChunkEmbeddingModel(
                chunk_id=other_chunk,
                embedding_space_id=active_space.id,
                embedding=vector(0.9, 0.1),
            ),
            ChunkEmbeddingModel(
                chunk_id=nonready_chunk,
                embedding_space_id=active_space.id,
                embedding=vector(1.0),
            ),
            ChunkEmbeddingModel(
                chunk_id=historical_chunk,
                embedding_space_id=active_space.id,
                embedding=vector(1.0),
            ),
            ChunkEmbeddingModel(
                chunk_id=scoped_chunk,
                embedding_space_id=inactive_space.id,
                embedding=vector(0.0, 1.0),
            ),
            ChunkEmbeddingModel(
                chunk_id=reference_chunk,
                embedding_space_id=active_space.id,
                embedding=vector(1.0),
            ),
        ]
    )
    await db_session.commit()

    repository = SqlHybridRetrievalRepository(db_session)
    english_query = RetrievalQuery(
        original="How is occlusion handled?",
        lexical_queries=("occlusion",),
        semantic_query="occlusion handling",
        scope=RetrievalScope(document_ids=(scoped_document, nonready_document)),
    )

    lexical = await repository.lexical_search(english_query, limit=10)
    semantic = await repository.vector_search(english_query, vector(1.0), limit=10)

    assert [candidate.chunk_id for candidate in lexical] == [scoped_chunk]
    assert lexical[0].lexical_rank == 1
    assert [candidate.chunk_id for candidate in semantic] == [scoped_chunk]
    assert semantic[0].vector_rank == 1

    chinese_query = RetrievalQuery(
        original="如何处理遮挡？",
        lexical_queries=("遮挡",),
        semantic_query="遮挡处理",
        scope=RetrievalScope(document_ids=(scoped_document,)),
    )
    chinese = await repository.lexical_search(chinese_query, limit=10)
    assert [candidate.chunk_id for candidate in chinese] == [chinese_chunk]
    assert chinese[0].content_type == "table"

    bilingual_query = RetrievalQuery(
        original="occlusion 遮挡",
        lexical_queries=("occlusion", "遮挡"),
        semantic_query="occlusion handling",
        scope=RetrievalScope(document_ids=(scoped_document,)),
    )
    bilingual_ids = {
        candidate.chunk_id
        for candidate in await repository.lexical_search(bilingual_query, limit=10)
    }
    assert bilingual_ids == {scoped_chunk, chinese_chunk}

    library_query = RetrievalQuery(
        original="occlusion",
        lexical_queries=("occlusion",),
        semantic_query="occlusion",
        scope=RetrievalScope(),
    )
    library_ids = {
        candidate.chunk_id
        for candidate in await repository.lexical_search(library_query, limit=10)
    }
    assert library_ids == {scoped_chunk, other_chunk}
    assert historical_chunk not in library_ids
    assert reference_chunk not in library_ids

    with pytest.raises(ValueError, match="positive"):
        await repository.lexical_search(library_query, limit=0)
    with pytest.raises(ValueError, match="1536"):
        await repository.vector_search(library_query, [1.0], limit=10)

    service = RetrievalService(
        repository,
        FakeEmbeddingProvider(dimension=1536),
        FakeRerankerProvider(scores={str(scoped_chunk): 1.0}),
    )
    service_result = await service.retrieve(english_query)
    assert [candidate.chunk_id for candidate in service_result] == [scoped_chunk]
