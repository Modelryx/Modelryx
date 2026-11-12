import uuid

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app

client = TestClient(app)


def test_part_routes_require_authentication() -> None:
    assert client.get("/api/v1/parts").status_code == 401


def test_revision_metadata_requires_schema_version() -> None:
    response = client.post(
        "/api/v1/parts/00000000-0000-0000-0000-000000000001/revisions",
        headers=AUTH,
        json={
            "revision_code": "A",
            "cad_system": "FreeCAD",
            "native_format": "FCStd",
            "metadata": {"properties": {}},
        },
    )
    assert response.status_code == 422


def test_favorites_path_is_not_parsed_as_an_item_uuid() -> None:
    response = client.get("/api/v1/items/favorites")
    assert response.status_code == 401


def test_create_read_update_part_and_create_revision() -> None:
    part_number = f"TEST-{uuid.uuid4().hex}"
    created = client.post(
        "/api/v1/parts",
        headers=AUTH,
        json={"part_number": part_number, "name": "Test Part", "category": "Tests"},
    )
    assert created.status_code == 201
    part = created.json()
    assert part["current_revision_id"] is None

    updated = client.patch(
        f"/api/v1/parts/{part['id']}",
        headers=AUTH,
        json={"description": "Updated"},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "Updated"

    revision_payload = {
        "revision_code": "A",
        "cad_system": "FreeCAD",
        "native_format": "FCStd",
        "metadata": {"schema_version": 1, "properties": {"material": "Steel"}},
    }
    revision = client.post(
        f"/api/v1/parts/{part['id']}/revisions", headers=AUTH, json=revision_payload
    )
    assert revision.status_code == 201
    assert revision.json()["state"] == "DRAFT"

    duplicate = client.post(
        f"/api/v1/parts/{part['id']}/revisions", headers=AUTH, json=revision_payload
    )
    assert duplicate.status_code == 409

    history = client.get(f"/api/v1/parts/{part['id']}/revisions", headers=AUTH)
    assert history.status_code == 200
    assert [item["revision_code"] for item in history.json()] == ["A"]


def test_incomplete_parts_are_hidden_from_default_list() -> None:
    part_number = f"HIDDEN-{uuid.uuid4().hex}"
    created = client.post(
        "/api/v1/parts",
        headers=AUTH,
        json={"part_number": part_number, "name": "Incomplete Part"},
    )
    assert created.status_code == 201

    default_list = client.get("/api/v1/parts", headers=AUTH)
    assert default_list.status_code == 200
    assert all(item["part_number"] != part_number for item in default_list.json()["items"])

    incomplete_search = client.post(
        "/api/v1/parts/search",
        headers=AUTH,
        json={"query": part_number, "include_incomplete": True},
    )
    assert incomplete_search.status_code == 200
    assert any(
        item["part"]["part_number"] == part_number for item in incomplete_search.json()["items"]
    )
