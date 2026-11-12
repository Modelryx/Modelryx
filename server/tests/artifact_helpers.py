import hashlib
import uuid
from typing import Any

from auth_helpers import AUTH


def commit_item(
    client,
    *,
    item_number: str,
    name: str,
    files: list[tuple[str, bytes]],
    item_type: str = "PART",
    bom: list[tuple[str, str, str]] | None = None,
    cad_system: str = "FreeCAD",
    native_format: str = "FCStd",
) -> dict[str, Any]:
    created = client.post(
        "/api/v1/staging",
        headers=AUTH,
        json={
            "client_staging_id": str(uuid.uuid4()),
            "files": [
                {
                    "logical_path": path,
                    "filename": path.rsplit("/", 1)[-1],
                    "mime_type": "application/octet-stream",
                    "byte_size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for path, content in files
            ],
        },
    )
    assert created.status_code == 201, created.text
    staging = created.json()
    staged_by_path = {row["logical_path"]: row for row in staging["files"]}
    for path, content in files:
        uploaded = client.put(
            staged_by_path[path]["upload_url"],
            headers={**AUTH, "Content-Type": "application/octet-stream"},
            content=content,
        )
        assert uploaded.status_code == 200, uploaded.text
    artifacts = []
    decisions = []
    for index, (path, _content) in enumerate(files):
        staged = staged_by_path[path]
        artifacts.append(
            {
                "client_artifact_id": str(uuid.uuid4()),
                "owner_staged_file_id": None if index == 0 else staged["id"],
                "artifact_type": (
                    "CAD_ASSEMBLY"
                    if index == 0 and item_type == "ASSEMBLY"
                    else "STEP"
                    if path.lower().endswith((".step", ".stp"))
                    else "CAD_MODEL"
                ),
                "name": name if index == 0 else staged["filename"],
                "classification": "AUTHORITATIVE",
                "relation_type": "SPECIFICATION",
                "is_primary": True,
                "files": [
                    {"staged_file_id": staged["id"], "reference_name": "MODEL", "role": "PRIMARY"}
                ],
            }
        )
        decision: dict[str, Any] = {"staged_file_id": staged["id"], "decision": "CREATE_NEW"}
        if index > 0:
            decision.update(
                {
                    "item": {
                        "item_number": f"{item_number}-C{index}",
                        "name": staged["filename"],
                        "item_type": "PART",
                    },
                    "revision": {
                        "revision_code": "A",
                        "cad_system": "FreeCAD",
                        "native_format": "FCStd",
                    },
                }
            )
        decisions.append(decision)
    proposal_bom = [
        {
            "parent_staged_file_id": staged_by_path[parent]["id"],
            "child_staged_file_id": staged_by_path[child]["id"],
            "instance_name": occurrence,
            "quantity": 1,
        }
        for parent, child, occurrence in (bom or [])
    ]
    proposed = client.put(
        f"/api/v1/staging/{staging['id']}/proposal",
        headers=AUTH,
        json={
            "expected_snapshot_version": staging["snapshot_version"],
            "operation": "CREATE_ITEM",
            "item": {"item_number": item_number, "name": name, "item_type": item_type},
            "revision": {
                "revision_code": "A",
                "cad_system": cad_system,
                "native_format": native_format,
            },
            "artifacts": artifacts,
            "identity_decisions": decisions,
            "bom": proposal_bom,
        },
    )
    assert proposed.status_code == 200, proposed.text
    staging = proposed.json()
    validated = client.post(f"/api/v1/staging/{staging['id']}/validate", headers=AUTH)
    assert validated.status_code == 200, validated.text
    ready = client.post(
        f"/api/v1/staging/{staging['id']}/ready",
        headers=AUTH,
        json={
            "expected_snapshot_version": staging["snapshot_version"],
        },
    )
    assert ready.status_code == 200, ready.text
    committed = client.post(
        f"/api/v1/uploads/{staging['id']}/commit",
        headers=AUTH,
        json={
            "snapshot_version": ready.json()["snapshot_version"],
            "commit_id": str(uuid.uuid4()),
        },
    )
    assert committed.status_code == 200, committed.text
    return committed.json()
