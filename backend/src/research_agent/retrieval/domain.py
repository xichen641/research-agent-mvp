from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    document_ids: tuple[UUID, ...] = ()
    document_version_ids: tuple[UUID, ...] = ()
    restrict_to_scope: bool = False

    def __post_init__(self) -> None:
        # Checkpoint serializers reconstruct sequence fields from msgpack arrays.
        # Normalize at the domain boundary so resumed graph state retains the tuple
        # invariant declared by this value object.
        object.__setattr__(self, "document_ids", tuple(self.document_ids))
        object.__setattr__(
            self, "document_version_ids", tuple(self.document_version_ids)
        )
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("document scope contains duplicates")
        if len(set(self.document_version_ids)) != len(self.document_version_ids):
            raise ValueError("version scope contains duplicates")


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    original: str
    lexical_queries: tuple[str, ...]
    semantic_query: str
    scope: RetrievalScope

    def __post_init__(self) -> None:
        if not self.original.strip():
            raise ValueError("original query must not be blank")
        if not self.lexical_queries or any(
            not query.strip() for query in self.lexical_queries
        ):
            raise ValueError("lexical queries must not be empty or blank")
        if not self.semantic_query.strip():
            raise ValueError("semantic query must not be blank")


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    title: str
    section_path: tuple[str, ...]
    content: str
    content_type: str
    page_start: int
    page_end: int
    lexical_rank: int | None = None
    vector_rank: int | None = None
    fused_score: float = 0.0
    rerank_score: float = 0.0
