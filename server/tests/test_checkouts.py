import uuid

from auth_helpers import AUTH
from fastapi.testclient import TestClient
from modelryx.api.main import app
from modelryx.core.auth import Principal, create_access_token
from modelryx.core.db import SessionLocal
from modelryx.models import (
    Item,
    Revision,
    RevisionContentSnapshot,
    User,
    WorkingVersion,
)
from sqlalchemy import select

client = TestClient(app)


def test_checkout_is_separate_and_idempotent() -> None:
    created = client.post(
        "/api/v1/parts",
        headers=AUTH,
        json={
            "part_number": f"CHECKOUT-{uuid.uuid4().hex}",
            "name": "Checkout test",
        },
    )
    assert created.status_code == 201
    part_id = created.json()["id"]

    acquired = client.post(f"/api/v1/parts/{part_id}/checkout", headers=AUTH)
    assert acquired.status_code == 201
    assert acquired.json()["part_id"] == part_id

    repeated = client.post(f"/api/v1/parts/{part_id}/checkout", headers=AUTH)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == acquired.json()["id"]

    part = client.get(f"/api/v1/parts/{part_id}", headers=AUTH)
    assert part.status_code == 200
    assert part.json()["checkout_status"] == "CHECKED_OUT_BY_ME"

    released = client.delete(f"/api/v1/parts/{part_id}/checkout", headers=AUTH)
    assert released.status_code == 204
    assert client.get(f"/api/v1/parts/{part_id}/checkout", headers=AUTH).status_code == 404


def test_specific_revision_checkout_is_visible_and_exclusive() -> None:
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        other = User(
            external_subject=f"checkout-viewer-{uuid.uuid4().hex}",
            display_name="Other Engineer",
            role="engineer",
            is_active=True,
            auth_version=0,
        )
        db.add(other)
        item = Item(
            part_number=f"REV-CHECKOUT-{uuid.uuid4().hex}",
            name="Revision checkout test",
            part_type="PART",
            created_by=owner.id,
        )
        db.add(item)
        db.flush()
        revisions = []
        for code in ("A", "B"):
            revision = Revision(
                part_id=item.id,
                revision_code=code,
                cad_system="FreeCAD",
                native_format="FCStd",
                metadata_json={"schema_version": 1, "properties": {}},
                state="READY",
                created_by=owner.id,
            )
            db.add(revision)
            db.flush()
            version = WorkingVersion(
                revision_id=revision.id,
                version_number=1,
                state="READY",
                created_by=owner.id,
            )
            db.add(version)
            db.flush()
            db.add(
                RevisionContentSnapshot(
                    revision_id=revision.id,
                    sequence=1,
                    commit_id=uuid.uuid4(),
                    created_by=owner.id,
                )
            )
            revision.current_working_version_id = version.id
            revisions.append(revision)
        item.current_revision_id = revisions[1].id
        item_id, old_id, current_id = item.id, revisions[0].id, revisions[1].id
        other_subject = other.external_subject
        db.commit()

    older_revision = client.post(
        f"/api/v1/items/{item_id}/revisions/{old_id}/checkout", headers=AUTH
    )
    assert older_revision.status_code == 201
    older_detail = client.get(f"/api/v1/items/{item_id}/revisions/{old_id}", headers=AUTH)
    assert older_detail.status_code == 200
    assert older_detail.json()["checkout_status"] == "CHECKED_OUT_BY_ME"
    rows = client.get(f"/api/v1/items/{item_id}/revisions", headers=AUTH).json()
    assert (
        next(row for row in rows if row["id"] == str(old_id))["checkout_status"]
        == "CHECKED_OUT_BY_ME"
    )
    assert (
        next(row for row in rows if row["id"] == str(current_id))["checkout_status"] == "AVAILABLE"
    )
    other_auth = {
        "Authorization": "Bearer "
        + create_access_token(
            Principal(
                subject=other_subject,
                display_name="Other Engineer",
                role="engineer",
            )
        )
    }
    acquired = client.post(
        f"/api/v1/items/{item_id}/revisions/{current_id}/checkout", headers=other_auth
    )
    assert acquired.status_code == 201
    rows = client.get(f"/api/v1/items/{item_id}/revisions", headers=AUTH).json()
    assert (
        next(row for row in rows if row["id"] == str(current_id))["checkout_status"]
        == "CHECKED_OUT_BY_OTHER"
    )
    assert (
        next(row for row in rows if row["id"] == str(old_id))["checkout_status"]
        == "CHECKED_OUT_BY_ME"
    )

    visible = client.get(f"/api/v1/items/{item_id}/revisions/{current_id}", headers=other_auth)
    assert visible.status_code == 200
    assert visible.json()["checkout_status"] == "CHECKED_OUT_BY_ME"
    other_rows = client.get(f"/api/v1/items/{item_id}/revisions", headers=other_auth).json()
    assert (
        next(row for row in other_rows if row["id"] == str(current_id))["checkout_status"]
        == "CHECKED_OUT_BY_ME"
    )
    blocked = client.post(f"/api/v1/items/{item_id}/revisions/{current_id}/checkout", headers=AUTH)
    assert blocked.status_code == 409
