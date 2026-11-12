from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app

client = TestClient(app)


def test_admin_can_create_and_select_template() -> None:
    suffix = __import__("uuid").uuid4().hex[:8]
    created = client.post(
        "/api/v1/admin/item-number-templates",
        headers=AUTH,
        json={
            "name": f"Test part numbering {suffix}",
            "part_pattern": f"T{suffix}-P-{{sequence:04d}}",
            "assembly_pattern": f"T{suffix}-A-{{sequence:04d}}",
            "part_next_value": 12,
            "assembly_next_value": 7,
        },
    )
    assert created.status_code == 201
    selected = client.patch(
        f"/api/v1/admin/item-number-templates/{created.json()['id']}",
        headers=AUTH,
        json={"is_selected": True},
    )
    assert selected.status_code == 200
    assert selected.json()["is_selected"] is True
    generated = client.post(
        "/api/v1/staging/item-numbers/generate", headers=AUTH, json={"item_type": "PART"}
    )
    assert generated.status_code == 200
    assert generated.json()["item_number"] == f"T{suffix}-P-0012"
    assert generated.json()["template_name"] == f"Test part numbering {suffix}"


def test_api_allocates_independent_unique_numbers() -> None:
    first = client.post(
        "/api/v1/staging/item-numbers/generate", headers=AUTH, json={"item_type": "PART"}
    )
    second = client.post(
        "/api/v1/staging/item-numbers/generate", headers=AUTH, json={"item_type": "PART"}
    )
    assembly = client.post(
        "/api/v1/staging/item-numbers/generate", headers=AUTH, json={"item_type": "ASSEMBLY"}
    )
    assert first.status_code == second.status_code == assembly.status_code == 200
    assert first.json()["item_number"] != second.json()["item_number"]
    assert first.json()["item_type"] == "PART"
    assert assembly.json()["item_type"] == "ASSEMBLY"
