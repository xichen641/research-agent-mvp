from collections.abc import Callable, Iterable

from research_agent.ingestion.contracts import ParsedElement
from research_agent.ingestion.models import ChunkDraft, ChunkingConfig


TokenCounter = Callable[[str], int]
_CJK_RANGES = (
    ("\u3400", "\u4dbf"),
    ("\u4e00", "\u9fff"),
    ("\uf900", "\ufaff"),
)
_REFERENCE_SECTIONS = {"references", "bibliography", "参考文献"}


class StructuralChunker:
    def __init__(
        self,
        config: ChunkingConfig | None = None,
        *,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self._config = config or ChunkingConfig()
        self._token_counter = token_counter or _default_token_count

    def chunk(self, elements: Iterable[ParsedElement]) -> list[ChunkDraft]:
        prepared = [
            _CountedElement(element, self._token_counter(element.text))
            for element in elements
            if element.text.strip()
        ]
        drafts: list[ChunkDraft] = []
        current: list[_CountedElement] = []

        for item in prepared:
            if current and item.element.section_path != current[0].element.section_path:
                self._append(drafts, current)
                current = []

            if item.token_count > self._config.target_tokens:
                if current:
                    self._append(drafts, current)
                    current = []
                # Oversized source elements stay atomic so provenance and semantics
                # are never split by an approximate token counter.
                # 超大源元素保持原子性，避免近似 token 计数拆散来源和语义。
                self._append(drafts, [item])
                continue

            if (
                current
                and _total_tokens(current) + item.token_count
                > self._config.target_tokens
            ):
                self._append(drafts, current)
                # Overlap copies only complete trailing elements; partial elements
                # would break provenance, while the incoming element's remaining
                # capacity also bounds the suffix so ordinary chunks stay on target.
                # 重叠只复制尾部完整元素；拆分会破坏来源，且后缀同时受新元素剩余容量限制，以保证普通切片不超标。
                remaining_capacity = self._config.target_tokens - item.token_count
                current = self._overlap(current, remaining_capacity)

            current.append(item)

        if current:
            self._append(drafts, current)
        return drafts

    def _overlap(
        self,
        elements: list["_CountedElement"],
        remaining_capacity: int,
    ) -> list["_CountedElement"]:
        overlap: list[_CountedElement] = []
        total = 0
        overlap_budget = min(
            self._config.overlap_tokens,
            remaining_capacity,
        )
        for item in reversed(elements):
            if total + item.token_count > overlap_budget:
                break
            overlap.append(item)
            total += item.token_count
        overlap.reverse()
        return overlap

    @staticmethod
    def _append(drafts: list[ChunkDraft], elements: list["_CountedElement"]) -> None:
        pages = [
            item.element.page_number
            for item in elements
            if item.element.page_number > 0
        ]
        drafts.append(
            ChunkDraft(
                chunk_index=len(drafts),
                section_path=elements[0].element.section_path,
                content="\n\n".join(item.element.text for item in elements),
                content_type=_content_type(elements),
                page_start=min(pages, default=0),
                page_end=max(pages, default=0),
                element_ids=tuple(item.element.element_id for item in elements),
                token_count=_total_tokens(elements),
            )
        )


class _CountedElement:
    __slots__ = ("element", "token_count")

    def __init__(self, element: ParsedElement, token_count: int) -> None:
        self.element = element
        self.token_count = token_count


def lexical_text(content: str) -> str:
    if not any(_is_cjk(character) for character in content):
        return content

    output: list[str] = []
    for index, character in enumerate(content):
        if not _is_cjk(character):
            output.append(character)
            continue
        if output and not output[-1].isspace():
            output.append(" ")
        output.append(character)
        if index + 1 < len(content) and not content[index + 1].isspace():
            output.append(" ")
    return "".join(output)


def _default_token_count(text: str) -> int:
    return len(lexical_text(text).split())


def _is_cjk(character: str) -> bool:
    return any(start <= character <= end for start, end in _CJK_RANGES)


def _total_tokens(elements: list[_CountedElement]) -> int:
    return sum(item.token_count for item in elements)


def _content_type(elements: list[_CountedElement]) -> str:
    section_path = elements[0].element.section_path
    if any(section.casefold() in _REFERENCE_SECTIONS for section in section_path):
        return "reference"
    kinds = {item.element.kind.casefold() for item in elements}
    if kinds == {"table"}:
        return "table"
    if kinds == {"picture"}:
        return "figure"
    return "text"
