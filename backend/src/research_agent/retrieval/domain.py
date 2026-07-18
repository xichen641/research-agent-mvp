from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    document_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("document scope contains duplicates")


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
    page_start: int
    page_end: int
    lexical_rank: int | None = None
    vector_rank: int | None = None
    fused_score: float = 0.0
    rerank_score: float = 0.0
