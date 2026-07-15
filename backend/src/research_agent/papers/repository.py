from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.papers.domain import (
    CreatedDocument,
    DocumentRecord,
    DocumentStatus,
    DocumentVersionRecord,
    VersionStatus,
)
from research_agent.papers.models import (
    DocumentModel,
    DocumentTaskModel,
    DocumentVersionModel,
)
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

    async def switch_current_version(
        self,
        document_id: EntityId,
        version_id: EntityId,
        status: DocumentStatus,
    ) -> None: ...

    async def commit(self) -> None: ...


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
        model = await self._session.get(DocumentVersionModel, version_id)
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
        )

    async def get_task(self, task_id: EntityId) -> TaskRecord | None:
        model = await self._session.get(DocumentTaskModel, task_id)
        if model is None:
            return None
        return _task_record(model)

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

    async def commit(self) -> None:
        await self._session.commit()

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


if TYPE_CHECKING:
    def _paper_repository_conformance(
        repository: SqlPaperRepository,
    ) -> PaperRepository:
        return repository
