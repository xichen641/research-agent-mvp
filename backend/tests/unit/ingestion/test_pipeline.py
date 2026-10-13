from datetime import timedelta
from io import BytesIO
from pathlib import Path
import threading
from typing import BinaryIO
from uuid import UUID

import pytest

from research_agent.ingestion.contracts import (
    BoundingBox,
    ParsedDocument,
    ParsedElement,
    ParsedImage,
)
from research_agent.ingestion.pipeline import IngestionPipeline
from research_agent.papers.domain import (
    DocumentStatus,
    DocumentVersionRecord,
    VersionStatus,
)
from research_agent.shared.errors import AppError
from research_agent.tasks.domain import TaskRecord, TaskStage, TaskStatus


DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000001")
VERSION_ID = UUID("00000000-0000-0000-0000-000000000002")
TASK_ID = UUID("00000000-0000-0000-0000-000000000003")


class TrackingStream(BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.read_threads: list[int] = []

    def read(self, size: int | None = -1) -> bytes:
        self.read_threads.append(threading.get_ident())
        return super().read(-1 if size is None else size)


class FakeStorage:
    def __init__(self) -> None:
        self.source = TrackingStream(b"%PDF-1.7\nsource")
        self.get_threads: list[int] = []
        self.puts: list[dict[str, object]] = []

    def get(self, *, key: str) -> BinaryIO:
        assert key == "papers/source.pdf"
        self.get_threads.append(threading.get_ident())
        return self.source

    def put(self, *, key: str, stream: BinaryIO, content_type: str) -> None:
        self.puts.append(
            {
                "key": key,
                "content": stream.read(),
                "content_type": content_type,
                "thread": threading.get_ident(),
            }
        )

    def delete(self, *, key: str) -> None:
        del key

    def presigned_get(self, *, key: str, expires: timedelta) -> str:
        del key, expires
        return "https://example.test/private"


class FakeParser:
    name = "fake-parser"
    version = "1.2.3"

    def __init__(self, parsed: ParsedDocument) -> None:
        self.parsed = parsed
        self.calls: list[Path] = []
        self.threads: list[int] = []

    def parse(self, source: Path) -> ParsedDocument:
        assert source.read_bytes() == b"%PDF-1.7\nsource"
        self.calls.append(source)
        self.threads.append(threading.get_ident())
        return self.parsed


class FakePapers:
    def __init__(self) -> None:
        self.version = DocumentVersionRecord(
            id=VERSION_ID,
            document_id=DOCUMENT_ID,
            filename="source.pdf",
            source_object_key="papers/source.pdf",
            parser_name="docling",
            parser_version="unparsed",
            pipeline_version="1",
            status=VersionStatus.PROCESSING,
        )
        self.persisted: list[dict[str, object]] = []
        self.failed: list[UUID] = []
        self.commit_calls = 0
        self.rollback_calls = 0

    async def get_version(self, version_id: UUID) -> DocumentVersionRecord | None:
        return self.version if version_id == VERSION_ID else None

    async def persist_parse_artifacts(
        self,
        version_id: UUID,
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
        self.persisted.append(
            {
                "version_id": version_id,
                "parser_name": parser_name,
                "parser_version": parser_version,
                "json_object_key": json_object_key,
                "markdown_object_key": markdown_object_key,
                "image_object_keys": image_object_keys,
                "parse_quality": parse_quality,
                "version_status": version_status,
                "document_status": document_status,
            }
        )

    async def mark_parse_failed(self, version_id: UUID) -> None:
        self.failed.append(version_id)

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


class FakeTasks:
    def __init__(self) -> None:
        self.task = TaskRecord(
            id=TASK_ID,
            document_version_id=VERSION_ID,
            stage=TaskStage.QUEUED,
            status=TaskStatus.QUEUED,
            progress_current=0,
            progress_total=len(TaskStage),
            retry_count=0,
            error_code="old-code",
            error_message="old-message",
        )
        self.updates: list[dict[str, object]] = []

    async def get_task_for_version(self, version_id: UUID) -> TaskRecord | None:
        return self.task if version_id == VERSION_ID else None

    async def update_task(
        self,
        task_id: UUID,
        *,
        stage: TaskStage,
        status: TaskStatus,
        progress_current: int,
        progress_total: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.updates.append(
            {
                "task_id": task_id,
                "stage": stage,
                "status": status,
                "progress_current": progress_current,
                "progress_total": progress_total,
                "error_code": error_code,
                "error_message": error_message,
            }
        )


def parsed_document() -> ParsedDocument:
    return ParsedDocument(
        markdown="# Heading\n\n" + "x" * 100,
        json_bytes=b'{"schema_name":"DoclingDocument"}',
        elements=[
            ParsedElement(
                element_id="heading",
                kind="section_header",
                text="Heading",
                section_path=("Heading",),
                page_number=1,
                bbox=BoundingBox(1, 2, 3, 4),
            ),
            ParsedElement(
                element_id="body",
                kind="text",
                text="x" * 100,
                section_path=("Heading",),
                page_number=1,
                bbox=BoundingBox(1, 5, 3, 9),
            ),
        ],
        images=[ParsedImage("figure/one", b"png", "image/png")],
        page_count=1,
    )


@pytest.mark.asyncio
async def test_parse_persists_deterministic_artifacts_and_running_state() -> None:
    event_loop_thread = threading.get_ident()
    storage = FakeStorage()
    parser = FakeParser(parsed_document())
    papers = FakePapers()
    tasks = FakeTasks()

    result = await IngestionPipeline(parser, storage, papers, tasks).parse(VERSION_ID)

    expected_json = f"papers/{DOCUMENT_ID}/parsed/{VERSION_ID}/document.json"
    expected_markdown = f"papers/{DOCUMENT_ID}/parsed/{VERSION_ID}/document.md"
    expected_image = f"papers/{DOCUMENT_ID}/images/{VERSION_ID}/figure_one.png"
    assert result.json_object_key == expected_json
    assert result.markdown_object_key == expected_markdown
    assert result.image_object_keys == (expected_image,)
    assert result.needs_review is False
    assert [(put["key"], put["content_type"]) for put in storage.puts] == [
        (expected_json, "application/json"),
        (expected_markdown, "text/markdown; charset=utf-8"),
        (expected_image, "image/png"),
    ]
    assert storage.source.closed
    assert storage.get_threads and event_loop_thread not in storage.get_threads
    assert storage.source.read_threads
    assert event_loop_thread not in storage.source.read_threads
    assert parser.threads and event_loop_thread not in parser.threads
    assert all(put["thread"] != event_loop_thread for put in storage.puts)
    assert parser.calls and not parser.calls[0].exists()
    assert papers.rollback_calls == 0
    assert papers.commit_calls == 2
    assert papers.failed == []
    assert len(papers.persisted) == 1
    persisted = papers.persisted[0]
    assert persisted["parser_name"] == "fake-parser"
    assert persisted["parser_version"] == "1.2.3"
    assert persisted["json_object_key"] == expected_json
    assert persisted["markdown_object_key"] == expected_markdown
    assert persisted["image_object_keys"] == (expected_image,)
    assert persisted["version_status"] is VersionStatus.PROCESSING
    assert persisted["document_status"] is DocumentStatus.PROCESSING
    assert tasks.updates == [
        {
            "task_id": TASK_ID,
            "stage": TaskStage.PARSING,
            "status": TaskStatus.RUNNING,
            "progress_current": 2,
            "progress_total": len(TaskStage),
            "error_code": None,
            "error_message": None,
        }
    ]


@pytest.mark.asyncio
async def test_missing_provenance_is_warned_and_excluded_from_parsed_pages() -> None:
    parsed = ParsedDocument(
        markdown="orphan",
        json_bytes=b"{}",
        elements=[
            ParsedElement(
                element_id="orphan",
                kind="text",
                text="x" * 100,
                section_path=(),
                page_number=0,
                bbox=None,
            )
        ],
        images=[],
        page_count=1,
        warnings=("adapter warning", "adapter warning"),
    )

    result = await IngestionPipeline(
        FakeParser(parsed), FakeStorage(), FakePapers(), FakeTasks()
    ).parse(VERSION_ID)

    assert result.quality.parsed_pages == 0
    assert result.quality.empty_pages == (1,)
    assert result.quality.warnings == (
        "adapter warning",
        "missing provenance for orphan",
        "low parsed page coverage",
    )
    assert result.needs_review is True


def quality_boundary_document(first_page_characters: int) -> ParsedDocument:
    elements = [
        ParsedElement(
            element_id=f"page-{page_number}",
            kind="text",
            text="x" * (first_page_characters if page_number == 1 else 100),
            section_path=(),
            page_number=page_number,
            bbox=BoundingBox(1, 2, 3, 4),
        )
        for page_number in range(1, 20)
    ]
    return ParsedDocument("content", b"{}", elements, [], 20)


@pytest.mark.asyncio
async def test_quality_thresholds_accept_95_percent_and_reject_one_short_char() -> None:
    accepted_papers = FakePapers()
    accepted_tasks = FakeTasks()
    accepted = await IngestionPipeline(
        FakeParser(quality_boundary_document(100)),
        FakeStorage(),
        accepted_papers,
        accepted_tasks,
    ).parse(VERSION_ID)

    review_papers = FakePapers()
    review_tasks = FakeTasks()
    review = await IngestionPipeline(
        FakeParser(quality_boundary_document(99)),
        FakeStorage(),
        review_papers,
        review_tasks,
    ).parse(VERSION_ID)

    assert accepted.quality.parsed_pages == 19
    assert accepted.quality.total_pages == 20
    assert accepted.needs_review is False
    assert accepted.quality.warnings == ()
    assert review.quality.body_characters == 1899
    assert review.needs_review is True
    assert review.quality.warnings == ("insufficient parsed body text",)
    assert review_papers.persisted[0]["version_status"] is VersionStatus.NEEDS_REVIEW
    assert review_papers.persisted[0]["document_status"] is DocumentStatus.NEEDS_REVIEW
    assert review_tasks.updates[-1]["status"] is TaskStatus.FAILED
    assert review_tasks.updates[-1]["error_code"] == "PARSE_QUALITY_REVIEW"
    assert review_tasks.updates[-1]["error_message"] == (
        "Parsed document requires quality review"
    )


@pytest.mark.asyncio
async def test_zero_page_zero_text_requires_review_without_division_error() -> None:
    parsed = ParsedDocument("", b"{}", [], [], 0)

    result = await IngestionPipeline(
        FakeParser(parsed), FakeStorage(), FakePapers(), FakeTasks()
    ).parse(VERSION_ID)

    assert result.quality.total_pages == 0
    assert result.quality.parsed_pages == 0
    assert result.quality.body_characters == 0
    assert result.quality.empty_pages == ()
    assert result.quality.warnings == (
        "document has no pages",
        "low parsed page coverage",
    )
    assert result.needs_review is True


@pytest.mark.asyncio
async def test_duplicate_sanitized_image_ids_fail_before_artifact_upload() -> None:
    base = parsed_document()
    parsed = ParsedDocument(
        base.markdown,
        base.json_bytes,
        base.elements,
        [
            ParsedImage("figure/a", b"one", "image/png"),
            ParsedImage("figure_a", b"two", "image/png"),
        ],
        base.page_count,
    )
    storage = FakeStorage()
    papers = FakePapers()
    tasks = FakeTasks()

    with pytest.raises(AppError) as error:
        await IngestionPipeline(FakeParser(parsed), storage, papers, tasks).parse(
            VERSION_ID
        )

    assert error.value.code == "PARSE_ARTIFACT_ID_COLLISION"
    assert error.value.message == "Parsed image identifiers are not unique"
    assert error.value.retryable is False
    assert storage.puts == []
    assert storage.source.closed
    assert papers.persisted == []
    assert papers.rollback_calls == 1
    assert papers.failed == [VERSION_ID]
    assert papers.commit_calls == 2
    assert tasks.updates[-1]["stage"] is TaskStage.PARSING
    assert tasks.updates[-1]["status"] is TaskStatus.FAILED
    assert tasks.updates[-1]["error_code"] == "PARSE_ARTIFACT_ID_COLLISION"
    assert tasks.updates[-1]["error_message"] == (
        "Parsed image identifiers are not unique"
    )


class IntermediateUploadFailureStorage(FakeStorage):
    def __init__(self) -> None:
        super().__init__()
        self.upload_streams: list[BinaryIO] = []

    def put(self, *, key: str, stream: BinaryIO, content_type: str) -> None:
        self.upload_streams.append(stream)
        if len(self.upload_streams) == 2:
            raise OSError(
                "https://minio.internal token=secret C:/private/source.pdf full text"
            )
        super().put(key=key, stream=stream, content_type=content_type)


@pytest.mark.asyncio
async def test_intermediate_upload_failure_cleans_and_persists_sanitized_failure() -> (
    None
):
    storage = IntermediateUploadFailureStorage()
    parser = FakeParser(parsed_document())
    papers = FakePapers()
    tasks = FakeTasks()

    with pytest.raises(AppError) as error:
        await IngestionPipeline(parser, storage, papers, tasks).parse(VERSION_ID)

    assert error.value.code == "PARSE_UPLOAD_FAILED"
    assert error.value.message == "Parsed artifacts could not be stored"
    assert error.value.retryable is True
    assert "minio" not in str(error.value).lower()
    assert "secret" not in str(error.value).lower()
    assert "source.pdf" not in str(error.value).lower()
    assert storage.source.closed
    assert storage.upload_streams
    assert all(stream.closed for stream in storage.upload_streams)
    assert parser.calls and not parser.calls[0].exists()
    assert papers.persisted == []
    assert papers.rollback_calls == 1
    assert papers.failed == [VERSION_ID]
    assert papers.commit_calls == 2
    assert tasks.updates[-1]["status"] is TaskStatus.FAILED
    assert tasks.updates[-1]["error_code"] == "PARSE_UPLOAD_FAILED"
    assert tasks.updates[-1]["error_message"] == (
        "Parsed artifacts could not be stored"
    )


class FailingParser(FakeParser):
    def parse(self, source: Path) -> ParsedDocument:
        self.calls.append(source)
        self.threads.append(threading.get_ident())
        raise RuntimeError(
            "token=secret C:/private/source.pdf full confidential paper text"
        )


class FailingTaskRead(FakeTasks):
    async def get_task_for_version(self, version_id: UUID) -> TaskRecord | None:
        del version_id
        raise RuntimeError("SELECT private_task token=read-secret")


class FailingRollbackPapers(FakePapers):
    async def rollback(self) -> None:
        self.rollback_calls += 1
        raise RuntimeError("ROLLBACK private_table token=rollback-secret")


class FailingMarkPapers(FakePapers):
    async def mark_parse_failed(self, version_id: UUID) -> None:
        del version_id
        raise RuntimeError("UPDATE private_document token=mark-secret")


class FailingFailureCommitPapers(FakePapers):
    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_calls == 2:
            raise RuntimeError("COMMIT private_transaction token=commit-secret")


class FailingFailureTaskUpdate(FakeTasks):
    async def update_task(
        self,
        task_id: UUID,
        *,
        stage: TaskStage,
        status: TaskStatus,
        progress_current: int,
        progress_total: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if status is TaskStatus.FAILED:
            raise RuntimeError("UPDATE private_task token=update-secret")
        await super().update_task(
            task_id,
            stage=stage,
            status=status,
            progress_current=progress_current,
            progress_total=progress_total,
            error_code=error_code,
            error_message=error_message,
        )


@pytest.mark.asyncio
async def test_task_read_failure_is_sanitized_and_rolled_back() -> None:
    papers = FakePapers()

    with pytest.raises(AppError) as error:
        await IngestionPipeline(
            FakeParser(parsed_document()),
            FakeStorage(),
            papers,
            FailingTaskRead(),
        ).parse(VERSION_ID)

    assert error.value.code == "PARSE_PERSISTENCE_FAILED"
    assert error.value.message == "Parse state could not be loaded"
    assert error.value.retryable is True
    assert "private_task" not in str(error.value)
    assert "secret" not in str(error.value).lower()
    assert papers.rollback_calls == 1


@pytest.mark.parametrize(
    ("papers", "tasks"),
    [
        pytest.param(FailingRollbackPapers(), FakeTasks(), id="rollback"),
        pytest.param(FailingMarkPapers(), FakeTasks(), id="mark-failed"),
        pytest.param(FakePapers(), FailingFailureTaskUpdate(), id="task-update"),
        pytest.param(FailingFailureCommitPapers(), FakeTasks(), id="failure-commit"),
    ],
)
@pytest.mark.asyncio
async def test_failure_recovery_errors_are_sanitized_and_cleanup_is_attempted(
    papers: FakePapers, tasks: FakeTasks
) -> None:
    with pytest.raises(AppError) as error:
        await IngestionPipeline(
            FailingParser(parsed_document()), FakeStorage(), papers, tasks
        ).parse(VERSION_ID)

    assert error.value.code == "PARSE_FAILURE_RECOVERY_FAILED"
    assert error.value.message == "Parse failure state could not be persisted"
    assert error.value.retryable is True
    assert "private" not in str(error.value).lower()
    assert "secret" not in str(error.value).lower()
    assert papers.rollback_calls == 2


@pytest.mark.asyncio
async def test_parser_failure_cleans_and_persists_sanitized_failure() -> None:
    storage = FakeStorage()
    parser = FailingParser(parsed_document())
    papers = FakePapers()
    tasks = FakeTasks()

    with pytest.raises(AppError) as error:
        await IngestionPipeline(parser, storage, papers, tasks).parse(VERSION_ID)

    assert error.value.code == "PARSE_PARSER_FAILED"
    assert error.value.message == "Source document could not be parsed"
    assert error.value.retryable is True
    assert "secret" not in str(error.value).lower()
    assert "source.pdf" not in str(error.value).lower()
    assert storage.source.closed
    assert parser.calls and not parser.calls[0].exists()
    assert papers.persisted == []
    assert papers.rollback_calls == 1
    assert papers.failed == [VERSION_ID]
    assert papers.commit_calls == 2
    assert tasks.updates[-1]["status"] is TaskStatus.FAILED
    assert tasks.updates[-1]["error_code"] == "PARSE_PARSER_FAILED"


class FailingReadStream(TrackingStream):
    def read(self, size: int | None = -1) -> bytes:
        del size
        self.read_threads.append(threading.get_ident())
        raise OSError("https://minio.internal token=secret C:/private/source.pdf")


class DownloadFailureStorage(FakeStorage):
    def __init__(self) -> None:
        super().__init__()
        self.source = FailingReadStream(b"source")


@pytest.mark.asyncio
async def test_download_failure_closes_stream_and_persists_sanitized_failure() -> None:
    event_loop_thread = threading.get_ident()
    storage = DownloadFailureStorage()
    parser = FakeParser(parsed_document())
    papers = FakePapers()
    tasks = FakeTasks()

    with pytest.raises(AppError) as error:
        await IngestionPipeline(parser, storage, papers, tasks).parse(VERSION_ID)

    assert error.value.code == "PARSE_DOWNLOAD_FAILED"
    assert error.value.message == "Source document could not be downloaded"
    assert error.value.retryable is True
    assert "minio" not in str(error.value).lower()
    assert "secret" not in str(error.value).lower()
    assert storage.source.closed
    assert storage.source.read_threads
    assert event_loop_thread not in storage.source.read_threads
    assert parser.calls == []
    assert storage.puts == []
    assert papers.persisted == []
    assert papers.rollback_calls == 1
    assert papers.failed == [VERSION_ID]
    assert papers.commit_calls == 2
    assert tasks.updates[-1]["status"] is TaskStatus.FAILED
    assert tasks.updates[-1]["error_code"] == "PARSE_DOWNLOAD_FAILED"


class ReusableStorage(FakeStorage):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[TrackingStream] = []

    def get(self, *, key: str) -> BinaryIO:
        assert key == "papers/source.pdf"
        self.get_threads.append(threading.get_ident())
        source = TrackingStream(b"%PDF-1.7\nsource")
        self.sources.append(source)
        return source


@pytest.mark.asyncio
async def test_retry_overwrites_same_ordered_keys_and_manifest() -> None:
    base = parsed_document()
    parsed = ParsedDocument(
        base.markdown,
        base.json_bytes,
        base.elements,
        [
            ParsedImage("z/image", b"z", "image/png"),
            ParsedImage("a/image", b"a", "image/png"),
        ],
        base.page_count,
    )
    storage = ReusableStorage()
    parser = FakeParser(parsed)
    papers = FakePapers()
    tasks = FakeTasks()
    pipeline = IngestionPipeline(parser, storage, papers, tasks)

    first = await pipeline.parse(VERSION_ID)
    second = await pipeline.parse(VERSION_ID)

    expected_images = (
        f"papers/{DOCUMENT_ID}/images/{VERSION_ID}/z_image.png",
        f"papers/{DOCUMENT_ID}/images/{VERSION_ID}/a_image.png",
    )
    assert first == second
    assert first.image_object_keys == expected_images
    first_put_keys = [put["key"] for put in storage.puts[:4]]
    second_put_keys = [put["key"] for put in storage.puts[4:]]
    assert first_put_keys == second_put_keys
    assert first_put_keys[-2:] == list(expected_images)
    assert len(papers.persisted) == 2
    assert papers.persisted[0]["image_object_keys"] == expected_images
    assert papers.persisted[1]["image_object_keys"] == expected_images
    assert papers.commit_calls == 4
    assert papers.rollback_calls == 0
    assert len(storage.sources) == 2
    assert all(source.closed for source in storage.sources)
    assert len(parser.calls) == 2
    assert all(not source.exists() for source in parser.calls)


@pytest.mark.asyncio
async def test_empty_and_dot_only_image_ids_use_stable_non_traversal_names() -> None:
    base = parsed_document()
    parsed = ParsedDocument(
        base.markdown,
        base.json_bytes,
        base.elements,
        [
            ParsedImage("", b"empty", "image/png"),
            ParsedImage(".", b"dot", "image/png"),
            ParsedImage("..", b"double-dot", "image/png"),
        ],
        base.page_count,
    )
    storage = ReusableStorage()
    pipeline = IngestionPipeline(FakeParser(parsed), storage, FakePapers(), FakeTasks())

    first = await pipeline.parse(VERSION_ID)
    second = await pipeline.parse(VERSION_ID)

    assert first.image_object_keys == second.image_object_keys
    assert len(set(first.image_object_keys)) == 3
    stems = [Path(key).stem for key in first.image_object_keys]
    assert all(stem not in {"", ".", ".."} for stem in stems)
    assert all(stem.startswith("image-") for stem in stems)
