from datetime import timedelta
from io import RawIOBase
from threading import Lock
from typing import BinaryIO, Protocol, cast

from minio import Minio
from minio.error import S3Error

from research_agent.config import Settings


class ObjectStorage(Protocol):
    def put(self, *, key: str, stream: BinaryIO, content_type: str) -> None: ...

    def get(self, *, key: str) -> BinaryIO: ...

    def delete(self, *, key: str) -> None: ...

    def presigned_get(self, *, key: str, expires: timedelta) -> str: ...


class _MinioResponse(Protocol):
    def read(self, size: int | None = None, /) -> bytes: ...

    def close(self) -> None: ...

    def release_conn(self) -> None: ...


class _MinioClient(Protocol):
    def bucket_exists(self, bucket_name: str) -> bool: ...

    def make_bucket(self, bucket_name: str) -> None: ...

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BinaryIO,
        length: int,
        content_type: str,
    ) -> object: ...

    def get_object(self, bucket_name: str, object_name: str) -> _MinioResponse: ...

    def remove_object(self, bucket_name: str, object_name: str) -> None: ...

    def presigned_get_object(
        self,
        bucket_name: str,
        object_name: str,
        expires: timedelta,
    ) -> str: ...


class _ResponseStream(RawIOBase):
    def __init__(self, response: _MinioResponse) -> None:
        super().__init__()
        self._response = response

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self._response.read()
        return self._response.read(size)

    def close(self) -> None:
        if not self.closed:
            try:
                self._response.close()
            finally:
                self._response.release_conn()
        super().close()


class MinioObjectStorage:
    def __init__(self, client: _MinioClient, bucket: str) -> None:
        self._client = client
        self._bucket = bucket
        self._bucket_ready = False
        self._bucket_lock = Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "MinioObjectStorage":
        return cls(
            Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=False,
            ),
            settings.minio_bucket,
        )

    def put(self, *, key: str, stream: BinaryIO, content_type: str) -> None:
        self._ensure_bucket()
        position = stream.tell()
        stream.seek(0, 2)
        length = stream.tell() - position
        stream.seek(position)
        self._client.put_object(
            self._bucket,
            key,
            stream,
            length,
            content_type=content_type,
        )

    def get(self, *, key: str) -> BinaryIO:
        response = cast(_MinioResponse, self._client.get_object(self._bucket, key))
        return cast(BinaryIO, _ResponseStream(response))

    def delete(self, *, key: str) -> None:
        self._client.remove_object(self._bucket, key)

    def presigned_get(self, *, key: str, expires: timedelta) -> str:
        return self._client.presigned_get_object(
            self._bucket,
            key,
            expires=expires,
        )

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        with self._bucket_lock:
            if self._bucket_ready:
                return
            if not self._client.bucket_exists(self._bucket):
                try:
                    self._client.make_bucket(self._bucket)
                except S3Error as error:
                    if error.code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                        raise
            self._bucket_ready = True
