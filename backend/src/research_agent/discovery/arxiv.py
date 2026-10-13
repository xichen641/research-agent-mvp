from datetime import datetime
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Protocol, cast
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import httpx

from research_agent.discovery.domain import (
    ArxivPaper,
    ArxivSearchPage,
    normalize_arxiv_id,
)
from research_agent.shared.errors import AppError


ATOM = "http://www.w3.org/2005/Atom"
OPEN_SEARCH = "http://a9.com/-/spec/opensearch/1.1/"
ARXIV = "http://arxiv.org/schemas/atom"
NAMESPACES = {"atom": ATOM, "opensearch": OPEN_SEARCH, "arxiv": ARXIV}


class ArxivClient(Protocol):
    async def search(
        self, *, topic: str, start: int, limit: int
    ) -> ArxivSearchPage: ...

    async def lookup(self, arxiv_id: str) -> ArxivPaper: ...

    async def download_pdf(self, paper: ArxivPaper, *, max_bytes: int) -> BinaryIO: ...


class HttpArxivClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_url: str = "https://export.arxiv.org/api/query",
    ) -> None:
        self._client = client
        self._api_url = api_url

    async def search(
        self, *, topic: str, start: int = 0, limit: int = 10
    ) -> ArxivSearchPage:
        normalized_topic = _normalize_topic(topic)
        if start < 0 or start > 10_000:
            raise ValueError("arXiv search start must be between 0 and 10000")
        if limit < 1 or limit > 50:
            raise ValueError("arXiv search limit must be between 1 and 50")
        content = await self._get_feed(
            {
                "search_query": f'all:"{normalized_topic}"',
                "start": str(start),
                "max_results": str(limit),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
        )
        return parse_arxiv_feed(content, topic=normalized_topic)

    async def lookup(self, arxiv_id: str) -> ArxivPaper:
        normalized_id = normalize_arxiv_id(arxiv_id)
        content = await self._get_feed(
            {"id_list": normalized_id, "start": "0", "max_results": "1"}
        )
        result = parse_arxiv_feed(content, topic=normalized_id)
        if not result.items:
            raise AppError("ARXIV_PAPER_NOT_FOUND", "The arXiv paper was not found")
        return result.items[0]

    async def download_pdf(self, paper: ArxivPaper, *, max_bytes: int) -> BinaryIO:
        if max_bytes <= 0:
            raise ValueError("maximum PDF size must be positive")
        _validate_download_url(paper.pdf_url)
        stream = SpooledTemporaryFile(max_size=min(max_bytes, 8 * 1024 * 1024))
        total = 0
        try:
            async with self._client.stream(
                "GET", paper.pdf_url, follow_redirects=True
            ) as response:
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > max_bytes:
                    raise AppError(
                        "ARXIV_PDF_TOO_LARGE",
                        "The selected arXiv PDF exceeds the import size limit",
                    )
                _validate_download_url(str(response.url))
                async for block in response.aiter_bytes(1024 * 1024):
                    total += len(block)
                    if total > max_bytes:
                        raise AppError(
                            "ARXIV_PDF_TOO_LARGE",
                            "The selected arXiv PDF exceeds the import size limit",
                        )
                    stream.write(block)
            stream.seek(0)
            return cast(BinaryIO, stream)
        except AppError:
            stream.close()
            raise
        except (httpx.HTTPError, ValueError):
            stream.close()
            raise AppError(
                "ARXIV_DOWNLOAD_FAILED",
                "The selected arXiv PDF could not be downloaded",
                retryable=True,
            ) from None

    async def _get_feed(self, params: dict[str, str]) -> bytes:
        try:
            response = await self._client.get(self._api_url, params=params)
            response.raise_for_status()
        except httpx.HTTPError:
            raise AppError(
                "ARXIV_API_UNAVAILABLE",
                "arXiv search is temporarily unavailable",
                retryable=True,
            ) from None
        content = response.content
        if len(content) > 5 * 1024 * 1024:
            raise AppError(
                "ARXIV_RESPONSE_TOO_LARGE",
                "The arXiv response exceeded the safe parsing limit",
                retryable=True,
            )
        return content


def parse_arxiv_feed(content: bytes, *, topic: str) -> ArxivSearchPage:
    upper = content[:10_000].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise AppError("ARXIV_INVALID_RESPONSE", "arXiv returned invalid XML")
    try:
        root = ET.fromstring(content)
        total_results = _integer(root, "opensearch:totalResults")
        start = _integer(root, "opensearch:startIndex")
        items_per_page = _integer(root, "opensearch:itemsPerPage")
        items = [_paper(entry) for entry in root.findall("atom:entry", NAMESPACES)]
    except (ET.ParseError, TypeError, ValueError):
        raise AppError("ARXIV_INVALID_RESPONSE", "arXiv returned invalid XML") from None
    return ArxivSearchPage(
        topic=topic,
        total_results=total_results,
        start=start,
        items_per_page=items_per_page,
        items=items,
    )


def _paper(entry: ET.Element) -> ArxivPaper:
    identifier = normalize_arxiv_id(_text(entry, "atom:id"))
    authors = [
        _clean_text(author.findtext("atom:name", namespaces=NAMESPACES) or "")
        for author in entry.findall("atom:author", NAMESPACES)
    ]
    authors = [author for author in authors if author]
    links = entry.findall("atom:link", NAMESPACES)
    abstract_url = _link(links, rel="alternate")
    pdf_url = _link(links, title="pdf")
    categories = [
        item.attrib["term"]
        for item in entry.findall("atom:category", NAMESPACES)
        if item.attrib.get("term")
    ]
    primary = entry.find("arxiv:primary_category", NAMESPACES)
    license_url = entry.findtext("arxiv:license", namespaces=NAMESPACES)
    return ArxivPaper(
        arxiv_id=identifier,
        title=_clean_text(_text(entry, "atom:title")),
        abstract=_clean_text(_text(entry, "atom:summary")),
        authors=authors,
        categories=categories,
        primary_category=None if primary is None else primary.attrib.get("term"),
        published_at=_timestamp(_text(entry, "atom:published")),
        updated_at=_timestamp(_text(entry, "atom:updated")),
        abstract_url=_https_url(abstract_url),
        pdf_url=_https_url(pdf_url),
        license_url=None if license_url is None else license_url.strip(),
    )


def _normalize_topic(topic: str) -> str:
    normalized = " ".join(topic.split()).replace('"', " ").strip()
    if not normalized or len(normalized) > 500:
        raise ValueError("arXiv topic must contain between 1 and 500 characters")
    return normalized


def _text(element: ET.Element, path: str) -> str:
    value = element.findtext(path, namespaces=NAMESPACES)
    if value is None or not value.strip():
        raise ValueError(f"missing arXiv field {path}")
    return value.strip()


def _integer(root: ET.Element, path: str) -> int:
    return int(_text(root, path))


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _link(
    links: list[ET.Element], *, rel: str | None = None, title: str | None = None
) -> str:
    for link in links:
        if rel is not None and link.attrib.get("rel") != rel:
            continue
        if title is not None and link.attrib.get("title") != title:
            continue
        href = link.attrib.get("href")
        if href:
            return href
    raise ValueError("missing arXiv link")


def _https_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))


def _validate_download_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in {
        "arxiv.org",
        "export.arxiv.org",
    }:
        raise AppError("ARXIV_INVALID_DOWNLOAD_URL", "The arXiv PDF URL is invalid")
