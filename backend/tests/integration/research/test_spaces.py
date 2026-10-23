from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.chat.domain import ModelKnowledgeBlock, VerifiedAnswer
from research_agent.chat.models import ChatSessionModel
from research_agent.chat.repository import SqlChatRepository
from research_agent.main import create_app
from research_agent.papers.models import DocumentModel, DocumentVersionModel
from research_agent.papers.repository import SqlPaperRepository
from research_agent.papers.routes import get_db_session
from research_agent.research.domain import SpaceInput, SpaceUpdate
from research_agent.research.repository import SqlResearchRepository
from research_agent.retrieval.domain import RetrievalScope
from research_agent.shared.errors import AppError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def paper(session: AsyncSession):
    return await SqlPaperRepository(session).create_document_with_version(
        title="ReID",
        sha256=uuid4().hex * 2,
        filename="paper.pdf",
        source_object_key="test/paper.pdf",
        parser_name="test",
        parser_version="1",
        pipeline_version="1",
    )


async def test_space_revision_processing_material_and_legacy_compatibility(db_session):
    created = await paper(db_session)
    repository = SqlResearchRepository(db_session)
    space = await repository.save(
        SpaceInput(
            name="  ReID  ", goal="语义引导", material_version_ids=[created.version_id]
        )
    )
    assert space.name == "ReID" and space.revision == 1
    assert space.materials[0].status == "PROCESSING"
    session = await repository.create_session(space.id, title="尚未解析也能开始讨论")
    assert session.research_space_id == space.id
    assert session.scope.document_version_ids == (created.version_id,)
    assert session.scope.restrict_to_scope is True
    updated = await repository.save(
        SpaceUpdate(
            name="ReID",
            goal="新的目标",
            expected_revision=1,
            material_version_ids=[created.version_id],
        ),
        space_id=space.id,
    )
    assert updated.revision == 2
    with pytest.raises(AppError, match="重新加载"):
        await repository.save(
            SpaceUpdate(name="旧页面", expected_revision=1), space_id=space.id
        )
    assert (await repository.get(space.id)).goal == "新的目标"
    assert (await repository.get(space.id)).revision == 2
    legacy = await SqlChatRepository(db_session).create_session(
        title="旧会话", scope=RetrievalScope()
    )
    assert legacy.research_space_id is None
    assert legacy.scope.restrict_to_scope is False


async def test_empty_space_is_restricted_and_other_space_is_separate(db_session):
    repository = SqlResearchRepository(db_session)
    empty = await repository.save(SpaceInput(name="空空间"))
    other = await repository.save(SpaceInput(name="另一空间"))
    chat = await repository.create_session(empty.id, title="讨论目标")
    assert chat.scope.restrict_to_scope and not chat.scope.document_ids
    assert await repository.sessions(other.id) == []
    assert [item.id for item in await repository.sessions(empty.id)] == [chat.id]


async def test_assignment_preserves_old_messages_and_rejects_running_or_reparenting(
    db_session,
):
    research = SqlResearchRepository(db_session)
    chats = SqlChatRepository(db_session)
    space = await research.save(SpaceInput(name="空间"))
    other = await research.save(SpaceInput(name="其他空间"))
    old = await chats.create_session(title="旧讨论", scope=RetrievalScope())
    run = await chats.start_run(session_id=old.id, question="原始问题")
    with pytest.raises(AppError) as running:
        await research.assign_session(space.id, old.id)
    assert running.value.code == "CHAT_RUN_IN_PROGRESS"
    await chats.complete_run(
        run_id=run.id,
        answer=VerifiedAnswer(blocks=[ModelKnowledgeBlock(text="原始回答")]),
    )
    original = await chats.list_messages(old.id)
    assigned = await research.assign_session(space.id, old.id)
    assert assigned.id == old.id and assigned.research_space_id == space.id
    assert await chats.list_messages(old.id) == original
    assert (await chats.get_run(run.id)).scope == RetrievalScope()
    assert (await research.assign_session(space.id, old.id)).id == old.id
    with pytest.raises(AppError) as conflict:
        await research.assign_session(other.id, old.id)
    assert conflict.value.code == "CHAT_SESSION_ALREADY_ASSIGNED"


async def test_new_runs_use_current_space_but_existing_run_snapshot_stays_fixed(
    db_session,
):
    first, second = await paper(db_session), await paper(db_session)
    research = SqlResearchRepository(db_session)
    chats = SqlChatRepository(db_session)
    space = await research.save(
        SpaceInput(
            name="空间",
            goal="旧目标",
            resource_constraints="24GB",
            material_version_ids=[first.version_id],
        )
    )
    chat = await research.create_session(space.id, title="讨论")
    run = await chats.start_run(
        session_id=chat.id, question="开始", scope=RetrievalScope()
    )
    assert run.scope.document_version_ids == (first.version_id,)
    assert run.research_context["revision"] == 1
    await research.save(
        SpaceUpdate(
            name="空间",
            goal="新目标",
            resource_constraints="12GB",
            expected_revision=1,
            material_version_ids=[second.version_id],
        ),
        space_id=space.id,
    )
    saved = await chats.get_run(run.id)
    assert saved.research_context["resource_constraints"] == "24GB"
    assert saved.scope.document_version_ids == (first.version_id,)
    await chats.complete_run(
        run_id=run.id, answer=VerifiedAnswer(blocks=[ModelKnowledgeBlock(text="回答")])
    )
    next_run = await chats.start_run(
        session_id=chat.id, question="继续", scope=chat.scope
    )
    assert next_run.scope.document_version_ids == (second.version_id,)
    assert next_run.research_context["revision"] == 2
    assert next_run.research_context["resource_constraints"] == "12GB"


async def test_material_scope_rejects_missing_and_duplicate_paper_versions(db_session):
    research = SqlResearchRepository(db_session)
    with pytest.raises(AppError) as missing:
        await research.save(
            SpaceInput(name="不存在的材料", material_version_ids=[uuid4()])
        )
    assert missing.value.code == "INVALID_SPACE_MATERIAL"
    created = await paper(db_session)
    second = DocumentVersionModel(
        id=uuid4(),
        document_id=created.document_id,
        filename="v2.pdf",
        source_object_key="test/v2.pdf",
        parser_name="test",
        parser_version="1",
        pipeline_version="1",
        status="PROCESSING",
    )
    db_session.add(second)
    await db_session.flush()
    with pytest.raises(AppError) as duplicate:
        await research.save(
            SpaceInput(
                name="双版本", material_version_ids=[created.version_id, second.id]
            )
        )
    assert duplicate.value.code == "INVALID_SPACE_MATERIAL"
    space = await research.save(
        SpaceInput(name="历史版本", material_version_ids=[created.version_id])
    )
    document = await db_session.get(DocumentModel, created.document_id)
    document.current_version_id = second.id
    await db_session.commit()
    assert (await research.get(space.id)).materials[
        0
    ].document_version_id == created.version_id
    assert (await research.get(space.id)).materials[0].is_current_version is False


async def test_space_http_contract_and_errors(db_session):
    app = create_app()

    async def database():
        yield db_session

    app.dependency_overrides[get_db_session] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/research/spaces",
            json={"name": "ReID", "resource_constraints": "12GB"},
        )
        assert response.status_code == 201, response.text
        space = response.json()
        base = f"/api/v1/research/spaces/{space['id']}"
        assert (await client.get(base)).json() == space
        assert len((await client.get("/api/v1/research/spaces")).json()["items"]) == 1
        assert (
            await client.post("/api/v1/research/spaces", json={"name": " "})
        ).status_code == 422
        assert (
            await client.get(f"/api/v1/research/spaces/{uuid4()}")
        ).status_code == 404
        assert (
            await client.post(
                "/api/v1/research/spaces",
                json={"name": "缺失材料", "material_version_ids": [str(uuid4())]},
            )
        ).status_code == 400
        body = {"name": "修改后", "expected_revision": 1}
        assert (await client.put(base, json=body)).json()["revision"] == 2
        assert (await client.put(base, json=body)).status_code == 409
        created = await client.post(base + "/sessions", json={"title": "继续讨论"})
        assert created.status_code == 201
        assert created.json()["research_space_id"] == space["id"]
        assert (await client.get(base + "/sessions")).json()["items"][0][
            "id"
        ] == created.json()["id"]
        session = await client.get(f"/api/v1/chat/sessions/{created.json()['id']}")
        assert session.json()["research_space_id"] == space["id"]
        record = await db_session.scalar(
            select(ChatSessionModel).where(ChatSessionModel.id == created.json()["id"])
        )
        assert record.scope["restrict_to_scope"] is True
