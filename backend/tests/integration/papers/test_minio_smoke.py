from datetime import timedelta
from io import BytesIO
import os
from uuid import uuid4

from minio import Minio
from minio.error import S3Error
import pytest

from research_agent.config import Settings
from research_agent.papers.storage import MinioObjectStorage


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MINIO_SMOKE") != "1",
        reason="set RUN_MINIO_SMOKE=1 to exercise the real MinIO adapter",
    ),
]


def test_authenticated_private_minio_round_trip() -> None:
    settings = Settings()
    storage = MinioObjectStorage.from_settings(settings)
    key = f"smoke/{uuid4()}.pdf"
    content = b"%PDF-1.7\nminio-smoke"
    uploaded = False

    try:
        storage.put(
            key=key,
            stream=BytesIO(content),
            content_type="application/pdf",
        )
        uploaded = True

        response = storage.get(key=key)
        try:
            assert response.read() == content
        finally:
            response.close()

        presigned = storage.presigned_get(
            key=key,
            expires=timedelta(minutes=5),
        )
        assert key in presigned

        anonymous = Minio(settings.minio_endpoint, secure=False)
        with pytest.raises(S3Error) as error:
            list(anonymous.list_objects(settings.minio_bucket))
        assert error.value.code in {"AccessDenied", "InvalidAccessKeyId"}
    finally:
        if uploaded:
            storage.delete(key=key)
