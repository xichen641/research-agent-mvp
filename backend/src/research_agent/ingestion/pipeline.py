import asyncio
from contextlib import closing
from dataclasses import asdict
import hashlib
from io import BytesIO
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from typing import Protocol, cast

from research_agent.ingestion.chunker import StructuralChunker
from research_agent.ingestion.contracts import (
    DocumentParser,
    ParsedDocument,
    ParseQualityReport,
    ParseResult,
)
from research_agent.ingestion.models import ChunkDraft
from research_agent.papers.domain import (
    DocumentStatus,
    DocumentVersionRecord,
    VersionStatus,
)
from research_agent.papers.storage import ObjectStorage
from research_agent.shared.errors import AppError
from research_agent.shared.ids import EntityId
from research_agent.tasks.domain import TaskRecord, TaskStage, TaskStatus


class ParsePaperRepository(Protocol):
    async def get_version(
        self, version_id: EntityId
    ) -> "DocumentVersionRecord | None": ...

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

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class ParseTaskRepository(Protocol):
    async def get_task_for_version(
        self, version_id: EntityId
    ) -> "TaskRecord | None": ...

    async def update_task(
        self,
        task_id: EntityId,
        *,
        stage: TaskStage,
        status: TaskStatus,
        progress_current: int,
        progress_total: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None: ...


class ChunkPaperRepository(Protocol):
    async def replace_chunks(
        self,
        version_id: EntityId,
        pipeline_version: str,
        chunks: list[ChunkDraft],
    ) -> None: ...


class IngestionPipeline:
    def __init__(
        self,
        parser: DocumentParser,
        storage: ObjectStorage,
        papers: ParsePaperRepository,
        tasks: ParseTaskRepository,
        chunker: StructuralChunker | None = None,
    ) -> None:
        self._parser = parser
        self._storage = storage
        self._papers = papers
        self._tasks = tasks
        self._chunker = chunker or StructuralChunker()

    async def parse(self, version_id: EntityId) -> ParseResult:
        version, task = await self._load_state(version_id)
        result, _parsed = await self._parse_loaded(version, task)
        return result

    async def run(self, version_id: EntityId) -> None:
        version, task = await self._load_state(version_id)
        if _stage_at_or_after(task.stage, TaskStage.EMBEDDING):
            return

        try:
            await self._update_running(task, TaskStage.VALIDATING, 1)
            await self._papers.commit()
        except Exception as error:
            application_error = AppError(
                "VALIDATION_FAILED",
                "Document could not be validated",
                retryable=True,
            )
            await self._persist_ingestion_failure(
                version_id, task.id, TaskStage.VALIDATING, 1, application_error
            )
            raise application_error from error

        result, parsed = await self._parse_loaded(version, task)
        if result.needs_review:
            return

        try:
            await self._update_running(task, TaskStage.NORMALIZING, 3)
            await self._papers.commit()
        except Exception as error:
            application_error = AppError(
                "NORMALIZATION_FAILED",
                "Document could not be normalized",
                retryable=True,
            )
            await self._persist_ingestion_failure(
                version_id, task.id, TaskStage.NORMALIZING, 3, application_error
            )
            raise application_error from error

        try:
            await self._update_running(task, TaskStage.CHUNKING, 4)
            await self._papers.commit()
            chunks = await asyncio.to_thread(self._chunker.chunk, parsed.elements)
        except Exception as error:
            application_error = AppError(
                "CHUNKING_FAILED",
                "Document chunks could not be created",
            )
            await self._persist_ingestion_failure(
                version_id, task.id, TaskStage.CHUNKING, 4, application_error
            )
            raise application_error from error

        try:
            await cast(ChunkPaperRepository, self._papers).replace_chunks(
                version_id, version.pipeline_version, chunks
            )
            await self._update_running(task, TaskStage.EMBEDDING, 5)
            # Chunk rows and the EMBEDDING handoff commit atomically so workers
            # never expose a task stage that has no corresponding chunk set.
            # 切片行与 EMBEDDING 交接原子提交，避免 worker 暴露没有对应切片集的任务阶段。
            await self._papers.commit()
        except Exception as error:
            application_error = AppError(
                "CHUNK_PERSISTENCE_FAILED",
                "Document chunks could not be persisted",
                retryable=True,
            )
            await self._persist_ingestion_failure(
                version_id, task.id, TaskStage.CHUNKING, 4, application_error
            )
            raise application_error from error

    async def _load_state(
        self, version_id: EntityId
    ) -> tuple[DocumentVersionRecord, TaskRecord]:
        try:
            version = await self._papers.get_version(version_id)
            task = await self._tasks.get_task_for_version(version_id)
        except Exception as error:
            await self._best_effort_rollback()
            raise AppError(
                "PARSE_PERSISTENCE_FAILED",
                "Parse state could not be loaded",
                retryable=True,
            ) from error
        if version is None or task is None:
            raise AppError(
                "PARSE_RECORD_NOT_FOUND",
                "Document version or parse task does not exist",
            )
        return version, task

    async def _parse_loaded(
        self,
        version: DocumentVersionRecord,
        task: TaskRecord,
    ) -> tuple[ParseResult, ParsedDocument]:
        version_id = version.id
        failure_code = "PARSE_PERSISTENCE_FAILED"
        failure_message = "Parse state could not be persisted"
        try:
            await self._tasks.update_task(
                task.id,
                stage=TaskStage.PARSING,
                status=TaskStatus.RUNNING,
                progress_current=2,
                progress_total=len(TaskStage),
                error_code=None,
                error_message=None,
            )
            await self._papers.commit()

            with TemporaryDirectory() as temporary_directory:
                source = Path(temporary_directory) / "source.pdf"
                failure_code = "PARSE_DOWNLOAD_FAILED"
                failure_message = "Source document could not be downloaded"
                await asyncio.to_thread(
                    self._download_source, version.source_object_key, source
                )
                failure_code = "PARSE_PARSER_FAILED"
                failure_message = "Source document could not be parsed"
                parsed = await asyncio.to_thread(self._parser.parse, source)
                report = _quality_report(parsed)
                needs_review = (
                    report.parsed_pages / max(report.total_pages, 1) < 0.95
                    or report.body_characters < report.parsed_pages * 100
                )

                json_key = (
                    f"papers/{version.document_id}/parsed/{version_id}/document.json"
                )
                markdown_key = (
                    f"papers/{version.document_id}/parsed/{version_id}/document.md"
                )
                safe_image_ids = [
                    _safe_path_segment(image.image_id) for image in parsed.images
                ]
                # Reject post-sanitization collisions before uploads so retries
                # remain idempotent and never silently overwrite another image.
                # 上传前拒绝清理后重名，确保重试幂等且不会静默覆盖其他图片。
                if len(safe_image_ids) != len(set(safe_image_ids)):
                    raise AppError(
                        "PARSE_ARTIFACT_ID_COLLISION",
                        "Parsed image identifiers are not unique",
                    )
                image_keys = tuple(
                    f"papers/{version.document_id}/images/{version_id}/"
                    f"{safe_image_id}.png"
                    for safe_image_id in safe_image_ids
                )

                failure_code = "PARSE_UPLOAD_FAILED"
                failure_message = "Parsed artifacts could not be stored"
                await self._put(json_key, parsed.json_bytes, "application/json")
                await self._put(
                    markdown_key,
                    parsed.markdown.encode("utf-8"),
                    "text/markdown; charset=utf-8",
                )
                for image, image_key in zip(
                    parsed.images, image_keys, strict=True
                ):
                    await self._put(
                        image_key, image.content, image.content_type
                    )

            failure_code = "PARSE_PERSISTENCE_FAILED"
            failure_message = "Parse result could not be persisted"
            version_status = (
                VersionStatus.NEEDS_REVIEW
                if needs_review
                else VersionStatus.PROCESSING
            )
            document_status = (
                DocumentStatus.NEEDS_REVIEW
                if needs_review
                else DocumentStatus.PROCESSING
            )
            await self._papers.persist_parse_artifacts(
                version_id,
                parser_name=self._parser.name,
                parser_version=self._parser.version,
                json_object_key=json_key,
                markdown_object_key=markdown_key,
                image_object_keys=image_keys,
                parse_quality=asdict(report),
                version_status=version_status,
                document_status=document_status,
            )
            if needs_review:
                await self._tasks.update_task(
                    task.id,
                    stage=TaskStage.PARSING,
                    status=TaskStatus.FAILED,
                    progress_current=2,
                    progress_total=len(TaskStage),
                    error_code="PARSE_QUALITY_REVIEW",
                    error_message="Parsed document requires quality review",
                )
            await self._papers.commit()
            return (
                ParseResult(
                    report, json_key, markdown_key, image_keys, needs_review
                ),
                parsed,
            )
        except Exception as error:
            application_error = (
                error
                if isinstance(error, AppError)
                else AppError(failure_code, failure_message, retryable=True)
            )
            await self._persist_failure_state(
                version_id, task.id, application_error
            )
            raise application_error from error

    async def _update_running(
        self, task: TaskRecord, stage: TaskStage, progress: int
    ) -> None:
        await self._tasks.update_task(
            task.id,
            stage=stage,
            status=TaskStatus.RUNNING,
            progress_current=progress,
            progress_total=len(TaskStage),
            error_code=None,
            error_message=None,
        )

    async def _persist_ingestion_failure(
        self,
        version_id: EntityId,
        task_id: EntityId,
        stage: TaskStage,
        progress: int,
        application_error: AppError,
    ) -> None:
        try:
            # Recovery starts only after partial work is rolled back, then writes
            # the sanitized terminal state in a fresh transaction.
            # 恢复会先回滚部分工作，再在新事务中写入脱敏的终止状态。
            await self._papers.rollback()
            await self._papers.mark_parse_failed(version_id)
            await self._tasks.update_task(
                task_id,
                stage=stage,
                status=TaskStatus.FAILED,
                progress_current=progress,
                progress_total=len(TaskStage),
                error_code=application_error.code,
                error_message=application_error.message,
            )
            await self._papers.commit()
        except Exception as recovery_error:
            await self._best_effort_rollback()
            raise AppError(
                "INGESTION_FAILURE_RECOVERY_FAILED",
                "Ingestion failure state could not be persisted",
                retryable=True,
            ) from recovery_error

    async def _persist_failure_state(
        self,
        version_id: EntityId,
        task_id: EntityId,
        application_error: AppError,
    ) -> None:
        try:
            # Roll back partial metadata before opening the durable failure transaction.
            # 在开启持久化失败状态的新事务前，先回滚部分元数据。
            await self._papers.rollback()
            await self._papers.mark_parse_failed(version_id)
            await self._tasks.update_task(
                task_id,
                stage=TaskStage.PARSING,
                status=TaskStatus.FAILED,
                progress_current=2,
                progress_total=len(TaskStage),
                error_code=application_error.code,
                error_message=application_error.message,
            )
            await self._papers.commit()
        except Exception as recovery_error:
            # A second rollback is cleanup only; its own failure stays internal.
            # 第二次回滚仅用于清理；即使它也失败，内部信息也不会外泄。
            await self._best_effort_rollback()
            raise AppError(
                "PARSE_FAILURE_RECOVERY_FAILED",
                "Parse failure state could not be persisted",
                retryable=True,
            ) from recovery_error

    async def _best_effort_rollback(self) -> None:
        try:
            await self._papers.rollback()
        except Exception:
            pass

    def _download_source(self, key: str, destination: Path) -> None:
        with closing(self._storage.get(key=key)) as stream:
            with destination.open("wb") as output:
                shutil.copyfileobj(stream, output)

    async def _put(self, key: str, content: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._put_content, key, content, content_type
        )

    def _put_content(
        self, key: str, content: bytes, content_type: str
    ) -> None:
        with BytesIO(content) as stream:
            self._storage.put(
                key=key, stream=stream, content_type=content_type
            )


def _quality_report(parsed: ParsedDocument) -> ParseQualityReport:
    parsed_pages = {
        element.page_number
        for element in parsed.elements
        if element.page_number > 0 and _non_whitespace_length(element.text) > 0
    }
    empty_pages = tuple(
        page for page in range(1, parsed.page_count + 1) if page not in parsed_pages
    )
    body_characters = sum(
        _non_whitespace_length(element.text) for element in parsed.elements
    )
    warnings = list(parsed.warnings)
    warnings.extend(
        f"missing provenance for {element.element_id}"
        for element in parsed.elements
        if element.page_number == 0
    )
    if parsed.page_count == 0:
        warnings.append("document has no pages")
    if len(parsed_pages) / max(parsed.page_count, 1) < 0.95:
        warnings.append("low parsed page coverage")
    if body_characters < len(parsed_pages) * 100:
        warnings.append("insufficient parsed body text")
    return ParseQualityReport(
        total_pages=parsed.page_count,
        parsed_pages=len(parsed_pages),
        body_characters=body_characters,
        empty_pages=empty_pages,
        section_count=sum(
            element.kind == "section_header" for element in parsed.elements
        ),
        table_count=sum(element.kind == "table" for element in parsed.elements),
        image_count=len(parsed.images),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _non_whitespace_length(value: str) -> int:
    return sum(not character.isspace() for character in value)


def _safe_path_segment(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", value)
    if sanitized.strip("."):
        return sanitized
    # Empty or dot-only names are unsafe path segments; a source-ID-derived name
    # is deterministic across retries while keeping distinct source IDs distinct.
    # 空值或纯点名称不是安全路径段；源 ID 派生名称可在重试间保持稳定并区分原始 ID。
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"image-{digest}"


def _stage_at_or_after(current: TaskStage, boundary: TaskStage) -> bool:
    stages = list(TaskStage)
    return stages.index(current) >= stages.index(boundary)
