import uuid

from auth_helpers import AUTH
from modelryx.core.db import SessionLocal
from modelryx.models import Item, MetadataExtraction, Revision
from test_engineering_search_api import _phase22_item, client


def test_tolerance_search_excludes_stale_counts_with_partial_drawing_evidence():
    token = uuid.uuid4().hex[:10]
    complete = _phase22_item(
        f"{token} complete", "", {"extraction": {"capabilities": {
            "gdt": "complete", "drawing_dimensions": "complete",
        }}}, part_number=f"TOL-{token}-OK",
    )
    partial = _phase22_item(
        f"{token} partial", "", {"extraction": {"capabilities": {
            "gdt": "partial", "drawing_dimensions": "complete",
        }}}, part_number=f"TOL-{token}-PARTIAL",
    )
    missing = _phase22_item(
        f"{token} missing", "", {}, part_number=f"TOL-{token}-MISSING",
    )
    with SessionLocal() as db:
        for item_id in (complete, partial, missing):
            item = db.get(Item, uuid.UUID(item_id))
            revision = db.get(Revision, item.current_revision_id)
            extraction = db.get(MetadataExtraction, revision.current_metadata_extraction_id)
            extraction.tolerance_count = 1  # Simulate a pre-existing derived index entry.
        db.commit()
    response = client.post(
        "/api/v1/search", headers=AUTH, json={"filters": {
            "item_number_regex": f"^TOL-{token}-", "tolerance_count": {"eq": 1},
        }},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    ids = {row["item_id"] for row in body["results"]}
    assert complete in ids and partial not in ids and missing not in ids
    assert body["coverage"]["excluded_unknown"] >= 2
