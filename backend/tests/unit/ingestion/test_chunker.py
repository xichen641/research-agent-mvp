import pytest

from research_agent.ingestion.chunker import StructuralChunker, lexical_text
from research_agent.ingestion.contracts import ParsedElement
from research_agent.ingestion.models import ChunkingConfig


def element(
    element_id: str,
    text: str,
    *,
    section: tuple[str, ...] = ("Methods",),
    kind: str = "text",
    page: int = 1,
) -> ParsedElement:
    return ParsedElement(element_id, kind, text, section, page, None)


def chunker(*, target: int = 5, overlap: int = 2) -> StructuralChunker:
    return StructuralChunker(
        ChunkingConfig(target_tokens=target, overlap_tokens=overlap),
        token_counter=lambda text: len(text.split()),
    )


@pytest.mark.parametrize(
    "config",
    [
        ChunkingConfig,
    ],
)
def test_chunking_config_is_frozen_and_slotted(config: type[ChunkingConfig]) -> None:
    value = config()

    with pytest.raises((AttributeError, TypeError)):
        value.target_tokens = 1  # type: ignore[misc]
    assert not hasattr(value, "__dict__")


@pytest.mark.parametrize(
    ("target", "overlap"),
    [(0, 0), (-1, 0), (10, -1), (10, 10), (10, 11)],
)
def test_chunking_config_rejects_invalid_values(target: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        ChunkingConfig(target_tokens=target, overlap_tokens=overlap)


def test_section_changes_flush_without_cross_section_overlap_and_skip_blank() -> None:
    chunks = chunker().chunk(
        [
            element("a", "one two"),
            element("blank", "  \n "),
            element("b", "three", section=("Results",), page=2),
        ]
    )

    assert [
        (chunk.chunk_index, chunk.section_path, chunk.content) for chunk in chunks
    ] == [
        (0, ("Methods",), "one two"),
        (1, ("Results",), "three"),
    ]
    assert [chunk.element_ids for chunk in chunks] == [("a",), ("b",)]


def test_overlap_carries_only_trailing_whole_elements_within_budget() -> None:
    chunks = chunker(target=5, overlap=2).chunk(
        [
            element("a", "one two three"),
            element("b", "four five"),
            element("c", "six seven"),
            element("d", "eight nine ten"),
        ]
    )

    assert [chunk.element_ids for chunk in chunks] == [
        ("a", "b"),
        ("b", "c"),
        ("c", "d"),
    ]
    assert [chunk.token_count for chunk in chunks] == [5, 4, 5]


def test_overlap_respects_incoming_element_remaining_capacity() -> None:
    chunks = chunker(target=10, overlap=4).chunk(
        [
            element("head", "one two three four five six"),
            element("tail", "seven eight nine ten"),
            element("incoming", "a b c d e f g h"),
        ]
    )

    assert [chunk.element_ids for chunk in chunks] == [
        ("head", "tail"),
        ("incoming",),
    ]
    assert [chunk.token_count for chunk in chunks] == [10, 8]
    assert all(chunk.token_count <= 10 for chunk in chunks)


def test_oversized_element_remains_atomic_and_does_not_block_following_content() -> (
    None
):
    chunks = chunker(target=3, overlap=1).chunk(
        [element("large", "one two three four"), element("small", "five")]
    )

    assert [chunk.element_ids for chunk in chunks] == [("large",), ("small",)]
    assert [chunk.token_count for chunk in chunks] == [4, 1]


def test_page_range_and_ordered_ids_use_positive_provenance_only() -> None:
    chunks = chunker(target=10).chunk(
        [
            element("unknown", "one", page=0),
            element("later", "two", page=4),
            element("earlier", "three", page=2),
        ]
    )

    assert chunks[0].page_start == 2
    assert chunks[0].page_end == 4
    assert chunks[0].element_ids == ("unknown", "later", "earlier")

    unknown = chunker(target=10).chunk([element("unknown", "one", page=0)])[0]
    assert (unknown.page_start, unknown.page_end) == (0, 0)


@pytest.mark.parametrize(
    ("section", "kinds", "expected"),
    [
        (("References",), ("table",), "reference"),
        (("Appendix", "BIBLIOGRAPHY"), ("picture",), "reference"),
        (("References in Chinese", "参考文献"), ("text",), "reference"),
        (("Methods",), ("table", "table"), "table"),
        (("Results",), ("picture", "picture"), "figure"),
        (("Results",), ("table", "text"), "text"),
    ],
)
def test_content_type_uses_section_and_element_semantics(
    section: tuple[str, ...], kinds: tuple[str, ...], expected: str
) -> None:
    elements = [
        element(str(index), f"word{index}", section=section, kind=kind)
        for index, kind in enumerate(kinds)
    ]

    assert chunker(target=10).chunk(elements)[0].content_type == expected


def test_lexical_text_preserves_english_and_adds_cjk_boundaries() -> None:
    english = "Alpha  beta\nGamma"

    assert lexical_text(english) == english
    assert lexical_text("Alpha研究 beta") == "Alpha 研 究 beta"


def test_default_counter_is_deterministic_for_mixed_text() -> None:
    chunks = StructuralChunker(
        ChunkingConfig(target_tokens=20, overlap_tokens=0)
    ).chunk([element("mixed", "Alpha 研究 beta")])

    assert chunks[0].token_count == 4
