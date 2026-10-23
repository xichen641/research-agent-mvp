from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.chat.domain import ChatSessionRecord
from research_agent.chat.models import ChatRunModel, ChatSessionModel
from research_agent.chat.repository import _session_record
from research_agent.papers.models import DocumentModel, DocumentVersionModel
from research_agent.research.domain import (
    ResearchSpace,
    SpaceInput,
    SpaceMaterial,
    SpaceUpdate,
)
from research_agent.research.models import (
    ResearchSpaceMaterialModel,
    ResearchSpaceModel,
)
from research_agent.shared.errors import AppError
from research_agent.shared.ids import new_id


class SqlResearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _space(self, space_id: UUID, *, lock: bool = False) -> ResearchSpaceModel:
        query = select(ResearchSpaceModel).where(ResearchSpaceModel.id == space_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        model = await self.session.scalar(query)
        if model is None:
            raise AppError("RESEARCH_SPACE_NOT_FOUND", "Research space was not found")
        return model

    async def get(self, space_id: UUID) -> ResearchSpace:
        model = await self._space(space_id)
        return await self._detail(model)

    async def list_spaces(self, *, limit: int = 100) -> list[ResearchSpace]:
        models = (
            await self.session.scalars(
                select(ResearchSpaceModel)
                .order_by(
                    ResearchSpaceModel.updated_at.desc(), ResearchSpaceModel.id.desc()
                )
                .limit(limit)
            )
        ).all()
        return [await self._detail(model) for model in models]

    async def save(
        self, body: SpaceInput, *, space_id: UUID | None = None
    ) -> ResearchSpace:
        versions = (
            await self.session.scalars(
                select(DocumentVersionModel)
                .join(
                    DocumentModel, DocumentModel.id == DocumentVersionModel.document_id
                )
                .where(
                    DocumentVersionModel.id.in_(body.material_version_ids),
                    DocumentModel.status != "DELETED",
                    DocumentVersionModel.status != "DELETED",
                )
            )
        ).all()
        if len(versions) != len(body.material_version_ids):
            raise AppError(
                "INVALID_SPACE_MATERIAL",
                "A selected paper version does not exist or was deleted",
            )
        if len({version.document_id for version in versions}) != len(versions):
            raise AppError(
                "INVALID_SPACE_MATERIAL", "Select only one version of each paper"
            )
        values = body.model_dump(exclude={"material_version_ids", "expected_revision"})
        if space_id is None:
            model = ResearchSpaceModel(id=new_id(), revision=1, **values)
            self.session.add(model)
            await self.session.flush()
        else:
            model = await self._space(space_id, lock=True)
            if (
                not isinstance(body, SpaceUpdate)
                or model.revision != body.expected_revision
            ):
                raise AppError(
                    "RESEARCH_SPACE_REVISION_CONFLICT",
                    "空间已被修改，请重新加载后再保存",
                )
            for key, value in values.items():
                setattr(model, key, value)
            model.revision += 1
            await self.session.execute(
                delete(ResearchSpaceMaterialModel).where(
                    ResearchSpaceMaterialModel.space_id == space_id
                )
            )
        self.session.add_all(
            [
                ResearchSpaceMaterialModel(
                    space_id=model.id,
                    document_id=version.document_id,
                    document_version_id=version.id,
                )
                for version in versions
            ]
        )
        await self.session.commit()
        await self.session.refresh(model)
        return await self._detail(model)

    async def sessions(self, space_id: UUID) -> list[ChatSessionRecord]:
        await self._space(space_id)
        models = (
            await self.session.scalars(
                select(ChatSessionModel)
                .where(ChatSessionModel.research_space_id == space_id)
                .order_by(
                    ChatSessionModel.updated_at.desc(), ChatSessionModel.id.desc()
                )
            )
        ).all()
        return [_session_record(model) for model in models]

    async def create_session(self, space_id: UUID, *, title: str) -> ChatSessionRecord:
        await self._space(space_id, lock=True)
        model = ChatSessionModel(
            id=new_id(),
            title=title,
            scope=await self.scope(space_id),
            research_space_id=space_id,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return _session_record(model)

    async def assign_session(
        self, space_id: UUID, session_id: UUID
    ) -> ChatSessionRecord:
        # Use the same chat -> space lock order as starting a turn.
        model = await self.session.scalar(
            select(ChatSessionModel)
            .where(ChatSessionModel.id == session_id)
            .with_for_update()
        )
        if model is None:
            raise AppError("CHAT_SESSION_NOT_FOUND", "Chat session was not found")
        await self._space(space_id, lock=True)
        if model.research_space_id == space_id:
            return _session_record(model)
        if model.research_space_id is not None:
            raise AppError("CHAT_SESSION_ALREADY_ASSIGNED", "会话已属于另一个研究空间")
        active = await self.session.scalar(
            select(ChatRunModel.id)
            .where(
                ChatRunModel.session_id == session_id,
                ChatRunModel.status.in_(("QUEUED", "RUNNING")),
            )
            .limit(1)
        )
        if active is not None:
            raise AppError("CHAT_RUN_IN_PROGRESS", "请等待当前讨论结束后再归入空间")
        model.research_space_id = space_id
        model.scope = await self.scope(space_id)
        await self.session.commit()
        await self.session.refresh(model)
        return _session_record(model)

    async def scope(self, space_id: UUID) -> dict[str, object]:
        links = (
            await self.session.scalars(
                select(ResearchSpaceMaterialModel)
                .where(ResearchSpaceMaterialModel.space_id == space_id)
                .order_by(ResearchSpaceMaterialModel.document_id)
            )
        ).all()
        return {
            "document_ids": [str(link.document_id) for link in links],
            "document_version_ids": [str(link.document_version_id) for link in links],
            "restrict_to_scope": True,
        }

    async def _detail(self, model: ResearchSpaceModel) -> ResearchSpace:
        rows = (
            await self.session.execute(
                select(DocumentModel, DocumentVersionModel)
                .join(
                    ResearchSpaceMaterialModel,
                    ResearchSpaceMaterialModel.document_id == DocumentModel.id,
                )
                .join(
                    DocumentVersionModel,
                    DocumentVersionModel.id
                    == ResearchSpaceMaterialModel.document_version_id,
                )
                .where(ResearchSpaceMaterialModel.space_id == model.id)
                .order_by(DocumentModel.title, DocumentModel.id)
            )
        ).all()
        return ResearchSpace(
            id=model.id,
            name=model.name,
            goal=model.goal,
            task_settings=model.task_settings,
            resource_constraints=model.resource_constraints,
            current_question=model.current_question,
            revision=model.revision,
            created_at=model.created_at,
            updated_at=model.updated_at,
            materials=[
                SpaceMaterial(
                    document_id=document.id,
                    document_version_id=version.id,
                    title=document.title,
                    filename=version.filename,
                    status="DELETED"
                    if document.status == "DELETED"
                    else version.status,
                    is_current_version=document.current_version_id == version.id,
                )
                for document, version in rows
            ],
        )
