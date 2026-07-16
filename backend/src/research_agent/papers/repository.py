from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sqlalchemy import Select, and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.papers.domain import (
    CreatedDocument,
    DocumentDetailRecord,
    DocumentRecord,
    DocumentSummaryRecord,
    DocumentStatus,
    DocumentVersionRecord,
    DuplicateDocumentError,
    PaperPersistenceError,
    SafeDocumentVersionRecord,
    UploadIds,
    VersionStatus,
)
from research_agent.papers.models import (
    DocumentModel,
    DocumentTaskModel,
    DocumentVersionModel,
    OutboxEventModel,
)
from research_agent.ingestion.chunker import lexical_text
from research_agent.ingestion.models import ChunkDraft, DocumentChunkModel
from research_agent.shared.ids import EntityId, new_id
from research_agent.tasks.domain import TaskRecord, TaskStage, TaskStatus


class PaperRecordNotFoundError(LookupError):
    pass


@runtime_checkable
class PaperRepository(Protocol):
    async def create_document_with_version(
        self,
        *,
        title: str,
        sha256: str,
        filename: str,
        source_object_key: str,
        parser_name: str,
        parser_version: str,
        pipeline_version: str,
    ) -> CreatedDocument: ...

    async def get_document(self, document_id: EntityId) -> DocumentRecord | None: ...

    async def find_by_sha256(self, sha256: str) -> DocumentRecord | None: ...

    async def get_version(
        self, version_id: EntityId
    ) -> DocumentVersionRecord | None: ...

    async def get_task(self, task_id: EntityId) -> TaskRecord | None: ...

    async def list_documents(
        self,
        *,
        limit: int,
        before_created_at: datetime | None = None,
        before_id: EntityId | None = None,
    ) -> list[DocumentSummaryRecord]: ...

    async def get_document_detail(
        self, document_id: EntityId
    ) -> DocumentDetailRecord | None: ...

    async def switch_current_version(
        self,
        document_id: EntityId,
        version_id: EntityId,
        status: DocumentStatus,
    ) -> None: ...

    async def persist_parse_artifacts(
        self,
        version_id: EntityId,
        *,
        parser_name: str,
        parser_version: str,
        json_object_key: str,
        markdown_object_key: str,
        image_object_keys: tuple[str, ...],
        parse_quality: dict[str, object],
        version_status: VersionStatus,
        document_status: DocumentStatus,
    ) -> None: ...

    async def mark_parse_failed(self, version_id: EntityId) -> None: ...

    async def replace_chunks(
        self,
        version_id: EntityId,
        pipeline_version: str,
        chunks: list[ChunkDraft],
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@runtime_checkable
class UploadPaperRepository(Protocol):
    async def find_by_sha256(self, sha256: str) -> DocumentRecord | None: ...

    async def create_upload(
        self,
        *,
        ids: UploadIds,
        title: str,
        filename: str,
        sha256: str,
        source_object_key: str,
        parser_name: str,
        parser_version: str,
        pipeline_version: str,
    ) -> None: ...

    async def add_outbox_event(
        self,
        *,
        event_id: EntityId,
        event_type: str,
        payload: dict[str, object],
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class SqlPaperRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_document_with_version(
        self,
        *,
        title: str,
        sha256: str,
        filename: str,
        source_object_key: str,
        parser_name: str,
        parser_version: str,
        pipeline_version: str,
    ) -> CreatedDocument:
        document_id = new_id()
        version_id = new_id()
        task_id = new_id()

        document = DocumentModel(
            id=document_id,
            title=title,
            sha256=sha256,
            status=DocumentStatus.PROCESSING,
            current_version_id=None,
        )
        self._session.add(document)
        await self._session.flush()

        version = DocumentVersionModel(
            id=version_id,
            document_id=document_id,
            filename=filename,
            source_object_key=source_object_key,
            parser_name=parser_name,
            parser_version=parser_version,
            pipeline_version=pipeline_version,
            status=VersionStatus.PROCESSING,
        )
        self._session.add(version)
        await self._session.flush()

        task = DocumentTaskModel(
            id=task_id,
            document_version_id=version_id,
            stage=TaskStage.QUEUED,
            status=TaskStatus.QUEUED,
            progress_current=0,
            progress_total=len(TaskStage),
            retry_count=0,
            error_code=None,
            error_message=None,
        )
        self._session.add(task)
        document.current_version_id = version_id

        return CreatedDocument(document_id, version_id, task_id)

    async def create_upload(
        self,
        *,
        ids: UploadIds,
        title: str,
        filename: str,
        sha256: str,
        source_object_key: str,
        parser_name: str,
        parser_version: str,
        pipeline_version: str,
    ) -> None:
        try:
            document = DocumentModel(
                id=ids.document_id,
                title=title,
                sha256=sha256,
                status=DocumentStatus.PROCESSING,
                current_version_id=None,
            )
            self._session.add(document)
            await self._session.flush()

            version = DocumentVersionModel(
                id=ids.version_id,
                document_id=ids.document_id,
                filename=filename,
                source_object_key=source_object_key,
                parser_name=parser_name,
                parser_version=parser_version,
                pipeline_version=pipeline_version,
                status=VersionStatus.PROCESSING,
            )
            self._session.add(version)
            await self._session.flush()

            self._session.add(
                DocumentTaskModel(
                    id=ids.task_id,
                    document_version_id=ids.version_id,
                    stage=TaskStage.QUEUED,
                    status=TaskStatus.QUEUED,
                    progress_current=0,
                    progress_total=len(TaskStage),
                    retry_count=0,
                    error_code=None,
                    error_message=None,
                )
            )
            document.current_version_id = ids.version_id
            await self._session.flush()
        except IntegrityError as error:
            if _constraint_name(error) == "uq_document_sha256_active":
                raise DuplicateDocumentError(sha256) from error
            raise PaperPersistenceError() from None

    async def add_outbox_event(
        self,
        *,
        event_id: EntityId,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        self._session.add(
            OutboxEventModel(
                id=event_id,
                event_type=event_type,
                payload=payload,
                attempts=0,
                available_at=datetime.now(UTC),
                sent_at=None,
            )
        )

    async def get_document(self, document_id: EntityId) -> DocumentRecord | None:
        return await self._get_document_record(
            self._document_query().where(DocumentModel.id == document_id)
        )

    async def find_by_sha256(self, sha256: str) -> DocumentRecord | None:
        return await self._get_document_record(
            self._document_query().where(
                DocumentModel.sha256 == sha256,
                DocumentModel.status != DocumentStatus.DELETED,
            )
        )

    async def get_version(
        self, version_id: EntityId
    ) -> DocumentVersionRecord | None:
        try:
            model = await self._session.get(DocumentVersionModel, version_id)
        except SQLAlchemyError as error:
            raise PaperPersistenceError() from error
        if model is None:
            return None
        return DocumentVersionRecord(
            id=model.id,
            document_id=model.document_id,
            filename=model.filename,
            source_object_key=model.source_object_key,
            parser_name=model.parser_name,
            parser_version=model.parser_version,
            pipeline_version=model.pipeline_version,
            status=VersionStatus(model.status),
            parsed_json_object_key=model.parsed_json_object_key,
            parsed_markdown_object_key=model.parsed_markdown_object_key,
            image_object_keys=(
                tuple(model.image_object_keys)
                if model.image_object_keys is not None
                else None
            ),
            parse_quality=model.parse_quality,
        )

    async def get_task(self, task_id: EntityId) -> TaskRecord | None:
        try:
            model = await self._session.get(DocumentTaskModel, task_id)
        except SQLAlchemyError as error:
            raise PaperPersistenceError() from error
        if model is None:
            return None
        return _task_record(model)

    async def list_documents(
        self,
        *,
        limit: int,
        before_created_at: datetime | None = None,
        before_id: EntityId | None = None,
    ) -> list[DocumentSummaryRecord]:
        statement = self._document_query().where(
            DocumentModel.status != DocumentStatus.DELETED
        )
        if before_created_at is not None and before_id is not None:
            statement = statement.where(
                or_(
                    DocumentModel.created_at < before_created_at,
                    and_(
                        DocumentModel.created_at == before_created_at,
                        DocumentModel.id < before_id,
                    ),
                )
            )
        statement = statement.order_by(
            DocumentModel.created_at.desc(), DocumentModel.id.desc()
        ).limit(limit)
        try:
            rows = (await self._session.execute(statement)).all()
        except SQLAlchemyError as error:
            raise PaperPersistenceError() from error
        return [_document_summary(document, task_id) for document, task_id in rows]

    async def get_document_detail(
        self, document_id: EntityId
    ) -> DocumentDetailRecord | None:
        statement = (
            self._document_query()
            .add_columns(DocumentVersionModel)
            .where(
                DocumentModel.id == document_id,
                DocumentModel.status != DocumentStatus.DELETED,
            )
        )
        try:
            row = (await self._session.execute(statement)).one_or_none()
        except SQLAlchemyError as error:
            raise PaperPersistenceError() from error
        if row is None:
            return None
        document, latest_task_id, version = row
        return DocumentDetailRecord(
            summary=_document_summary(document, latest_task_id),
            current_version=SafeDocumentVersionRecord(
                id=version.id,
                filename=version.filename,
                status=VersionStatus(version.status),
                pipeline_version=version.pipeline_version,
                created_at=version.created_at,
            ),
        )

    async def switch_current_version(
        self,
        document_id: EntityId,
        version_id: EntityId,
        status: DocumentStatus,
    ) -> None:
        document = await self._session.get(DocumentModel, document_id)
        version = await self._session.get(DocumentVersionModel, version_id)
        if document is None:
            raise PaperRecordNotFoundError(f"document {document_id} does not exist")
        if version is None or version.document_id != document_id:
            raise PaperRecordNotFoundError(
                f"version {version_id} does not belong to document {document_id}"
            )
        document.current_version_id = version_id
        document.status = status

    async def persist_parse_artifacts(
        self,
        version_id: EntityId,
        *,
        parser_name: str,
        parser_version: str,
        json_object_key: str,
        markdown_object_key: str,
        image_object_keys: tuple[str, ...],
        parse_quality: dict[str, object],
        version_status: VersionStatus,
        document_status: DocumentStatus,
    ) -> None:
        try:
            version = await self._session.get(DocumentVersionModel, version_id)
            if version is None:
                raise PaperRecordNotFoundError("document version does not exist")
            document = await self._session.get(DocumentModel, version.document_id)
            if document is None:
                raise PaperRecordNotFoundError("document does not exist")
            # The manifest and both statuses share one transaction boundary.
            # 清单和两个状态共享同一个事务边界。
            version.parser_name = parser_name
            version.parser_version = parser_version
            version.parsed_json_object_key = json_object_key
            version.parsed_markdown_object_key = markdown_object_key
            version.image_object_keys = list(image_object_keys)
            version.parse_quality = parse_quality
            version.status = version_status
            document.status = document_status
        except PaperRecordNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise PaperPersistenceError() from error

    async def mark_parse_failed(self, version_id: EntityId) -> None:
        try:
            version = await self._session.get(DocumentVersionModel, version_id)
            if version is None:
                raise PaperRecordNotFoundError("document version does not exist")
            document = await self._session.get(DocumentModel, version.document_id)
            if document is None:
                raise PaperRecordNotFoundError("document does not exist")
            version.status = VersionStatus.FAILED
            document.status = DocumentStatus.FAILED
        except PaperRecordNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise PaperPersistenceError() from error

    async def replace_chunks(
        self,
        version_id: EntityId,
        pipeline_version: str,
        chunks: list[ChunkDraft],
    ) -> None:
        if [chunk.chunk_index for chunk in chunks] != list(range(len(chunks))):
            raise ValueError("chunk indexes must be contiguous and zero-based")

        try:
            if chunks:
                values = [
                    {
                        "id": new_id(),
                        "document_version_id": version_id,
                        "chunk_index": chunk.chunk_index,
                        "section_path": list(chunk.section_path),
                        "content": chunk.content,
                        "lexical_text": lexical_text(chunk.content),
                        "content_type": chunk.content_type,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                        "element_ids": list(chunk.element_ids),
                        "token_count": chunk.token_count,
                        "pipeline_version": pipeline_version,
                    }
                    for chunk in chunks
                ]
                statement = insert(DocumentChunkModel).values(values)
                # The conflict update deliberately excludes the primary key so
                # infrastructure retries preserve stable chunk row identities.
                # 冲突更新刻意排除主键，以便基础设施重试时保留稳定的切片行标识。
                await self._session.execute(
                    statement.on_conflict_do_update(
                        constraint="uq_chunk_version_index_pipeline",
                        set_={
                            "section_path": statement.excluded.section_path,
                            "content": statement.excluded.content,
                            "lexical_text": statement.excluded.lexical_text,
                            "content_type": statement.excluded.content_type,
                            "page_start": statement.excluded.page_start,
                            "page_end": statement.excluded.page_end,
                            "element_ids": statement.excluded.element_ids,
                            "token_count": statement.excluded.token_count,
                        },
                    )
                )

            stale = delete(DocumentChunkModel).where(
                DocumentChunkModel.document_version_id == version_id,
                DocumentChunkModel.pipeline_version == pipeline_version,
            )
            if chunks:
                stale = stale.where(
                    DocumentChunkModel.chunk_index.not_in(
                        [chunk.chunk_index for chunk in chunks]
                    )
                )
            # Upsert and stale deletion share the caller's transaction so a
            # failed replacement leaves the previously committed set intact.
            # Upsert 与过期删除共用调用方事务，因此替换失败不会破坏已提交的旧集合。
            await self._session.execute(stale)
        except SQLAlchemyError:
            raise PaperPersistenceError() from None

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except SQLAlchemyError:
            raise PaperPersistenceError() from None

    async def rollback(self) -> None:
        try:
            await self._session.rollback()
        except SQLAlchemyError:
            raise PaperPersistenceError() from None

    @staticmethod
    def _document_query() -> Select[tuple[DocumentModel, EntityId]]:
        latest_task_id = (
            select(DocumentTaskModel.id)
            .where(
                DocumentTaskModel.document_version_id
                == DocumentModel.current_version_id
            )
            .order_by(DocumentTaskModel.created_at.desc(), DocumentTaskModel.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        return (
            select(DocumentModel, latest_task_id)
            .join(
                DocumentVersionModel,
                DocumentVersionModel.id == DocumentModel.current_version_id,
            )
            .where(latest_task_id.is_not(None))
        )

    async def _get_document_record(
        self, statement: Select[tuple[DocumentModel, EntityId]]
    ) -> DocumentRecord | None:
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        document, latest_task_id = row
        return DocumentRecord(
            id=document.id,
            title=document.title,
            sha256=document.sha256,
            status=DocumentStatus(document.status),
            current_version_id=document.current_version_id,
            latest_task_id=latest_task_id,
        )


def _task_record(model: DocumentTaskModel) -> TaskRecord:
    return TaskRecord(
        id=model.id,
        document_version_id=model.document_version_id,
        stage=TaskStage(model.stage),
        status=TaskStatus(model.status),
        progress_current=model.progress_current,
        progress_total=model.progress_total,
        retry_count=model.retry_count,
        error_code=model.error_code,
        error_message=model.error_message,
    )


def _document_summary(
    model: DocumentModel, latest_task_id: EntityId
) -> DocumentSummaryRecord:
    if model.current_version_id is None:
        raise PaperPersistenceError()
    return DocumentSummaryRecord(
        id=model.id,
        title=model.title,
        status=DocumentStatus(model.status),
        current_version_id=model.current_version_id,
        latest_task_id=latest_task_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _constraint_name(error: IntegrityError) -> str | None:
    current: BaseException | None = error.orig
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        constraint_name = getattr(current, "constraint_name", None)
        if isinstance(constraint_name, str):
            return constraint_name
        current = current.__cause__ or current.__context__
    return None


if TYPE_CHECKING:
    def _paper_repository_conformance(
        repository: SqlPaperRepository,
    ) -> PaperRepository:
        return repository

    def _upload_repository_conformance(
        repository: SqlPaperRepository,
    ) -> UploadPaperRepository:
        return repository
