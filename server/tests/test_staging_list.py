import hashlib
import uuid

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app

client = TestClient(app)


def test_staging_list_materializes_file_counts() -> None:
    content = b"staging-list-regression"
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "files": [
                {
                    "logical_path": "list-test.FCStd",
                    "filename": "list-test.FCStd",
                    "mime_type": "application/octet-stream",
                    "byte_size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    staging_id = created.json()["id"]

    response = client.get("/api/v1/staging?state=all&limit=100", headers=AUTH)

    assert response.status_code == 200, response.text
    listed = next(item for item in response.json()["items"] if item["id"] == staging_id)
    assert listed["file_count"] == 1
