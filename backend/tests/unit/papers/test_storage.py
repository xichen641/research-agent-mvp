from datetime import timedelta
from io import BytesIO
from typing import BinaryIO

from research_agent.papers.storage import MinioObjectStorage


class FakeResponse(BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.release_calls = 0

    def release_conn(self) -> None:
        self.release_calls += 1


class FakeMinioClient:
    def __init__(self, *, bucket_exists: bool = True) -> None:
        self.exists = bucket_exists
        self.bucket_exists_calls: list[str] = []
        self.make_bucket_calls: list[str] = []
        self.put_object_calls: list[dict[str, object]] = []
        self.get_object_calls: list[tuple[str, str]] = []
        self.remove_object_calls: list[tuple[str, str]] = []
        self.presign_calls: list[tuple[str, str, timedelta]] = []
        self.response = FakeResponse(b"stored")

    def bucket_exists(self, bucket_name: str) -> bool:
        self.bucket_exists_calls.append(bucket_name)
        return self.exists

    def make_bucket(self, bucket_name: str) -> None:
        self.make_bucket_calls.append(bucket_name)
        self.exists = True

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BinaryIO,
        length: int,
        content_type: str,
    ) -> None:
        self.put_object_calls.append(
            {
                "bucket": bucket_name,
                "key": object_name,
                "content": data.read(),
                "length": length,
                "content_type": content_type,
            }
        )

    def get_object(self, bucket_name: str, object_name: str) -> FakeResponse:
        self.get_object_calls.append((bucket_name, object_name))
        return self.response

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        self.remove_object_calls.append((bucket_name, object_name))

    def presigned_get_object(
        self, bucket_name: str, object_name: str, expires: timedelta
    ) -> str:
        self.presign_calls.append((bucket_name, object_name, expires))
        return "https://example.test/presigned"


def test_put_creates_missing_private_bucket_and_forwards_remaining_length() -> None:
    client = FakeMinioClient(bucket_exists=False)
    storage = MinioObjectStorage(client, "papers")
    stream = BytesIO(b"prefix-content")
    stream.seek(7)

    storage.put(key="generated.pdf", stream=stream, content_type="application/pdf")

    assert client.bucket_exists_calls == ["papers"]
    assert client.make_bucket_calls == ["papers"]
    assert client.put_object_calls == [
        {
            "bucket": "papers",
            "key": "generated.pdf",
            "content": b"content",
            "length": 7,
            "content_type": "application/pdf",
        }
    ]
    assert not hasattr(client, "set_bucket_policy")


def test_existing_bucket_is_not_created_again() -> None:
    client = FakeMinioClient(bucket_exists=True)
    storage = MinioObjectStorage(client, "papers")

    storage.put(
        key="generated.pdf", stream=BytesIO(b"data"), content_type="application/pdf"
    )

    assert client.make_bucket_calls == []


def test_get_returns_stream_that_closes_and_releases_response() -> None:
    client = FakeMinioClient()
    storage = MinioObjectStorage(client, "papers")

    result = storage.get(key="generated.pdf")
    assert result.read() == b"stored"
    result.close()

    assert client.get_object_calls == [("papers", "generated.pdf")]
    assert client.response.closed is True
    assert client.response.release_calls == 1


def test_delete_and_presign_forward_to_configured_bucket() -> None:
    client = FakeMinioClient()
    storage = MinioObjectStorage(client, "papers")
    expires = timedelta(minutes=15)

    storage.delete(key="generated.pdf")
    url = storage.presigned_get(key="generated.pdf", expires=expires)

    assert client.remove_object_calls == [("papers", "generated.pdf")]
    assert client.presign_calls == [("papers", "generated.pdf", expires)]
    assert url == "https://example.test/presigned"
