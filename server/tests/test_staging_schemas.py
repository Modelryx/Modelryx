import hashlib
import uuid

import pytest
from modelryx.api.staging import (
    StagingBomLineProposal,
    StagingCreateRequest,
    StagingPromoteRequest,
    _duplicate_bom_occurrence_groups,
)
from modelryx.models import BomLine
from pydantic import ValidationError


def file(path: str, content: bytes = b"cad") -> dict:
    return {
        "logical_path": path,
        "filename": path.replace("\\", "/").split("/")[-1],
        "byte_size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def test_staging_manifest_normalizes_paths_and_hashes() -> None:
    entry = file("components\\housing.step")
    entry["sha256"] = entry["sha256"].upper()
    payload = StagingCreateRequest(client_staging_id=uuid.uuid4(), files=[entry])
    assert payload.files[0].logical_path == "components/housing.step"
    assert payload.files[0].sha256 == entry["sha256"].lower()


@pytest.mark.parametrize("path", ["../secret.step", "a/../../secret.step", "/", "C:/secret.step"])
def test_staging_manifest_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        StagingCreateRequest(client_staging_id=uuid.uuid4(), files=[file(path)])


def test_staging_manifest_rejects_case_collisions() -> None:
    with pytest.raises(ValidationError):
        StagingCreateRequest(
            client_staging_id=uuid.uuid4(),
            files=[file("Part.step", b"a"), file("part.step", b"b")],
        )


def test_staging_promotion_requires_safe_item_number() -> None:
    promotion = StagingPromoteRequest(item_number="PF-100", name="Housing")
    assert promotion.revision_code == "A"
    with pytest.raises(ValidationError):
        StagingPromoteRequest(item_number="unsafe number", name="Housing")


def test_bom_occurrence_names_are_scoped_to_their_parent_assembly() -> None:
    root = uuid.uuid4()
    nested = uuid.uuid4()
    lines = [
        StagingBomLineProposal(
            parent_staged_file_id=root,
            child_staged_file_id=nested,
            instance_name="Wheel:1",
        ),
        StagingBomLineProposal(
            parent_staged_file_id=nested,
            child_staged_file_id=uuid.uuid4(),
            instance_name="Wheel:1",
        ),
    ]

    assert _duplicate_bom_occurrence_groups(lines) == []


def test_bom_occurrence_names_must_be_unique_within_one_parent() -> None:
    root = uuid.uuid4()
    lines = [
        StagingBomLineProposal(
            parent_staged_file_id=root,
            child_staged_file_id=uuid.uuid4(),
            instance_name="Wheel:1",
        ),
        StagingBomLineProposal(
            parent_staged_file_id=root,
            child_staged_file_id=uuid.uuid4(),
            instance_name="wheel:1",
        ),
    ]

    assert _duplicate_bom_occurrence_groups(lines) == [lines]


def test_bom_database_uniqueness_is_scoped_to_parent_revision() -> None:
    constraint = next(
        item
        for item in BomLine.__table__.constraints
        if item.name == "uq_bom_lines_occurrence"
    )

    assert [column.name for column in constraint.columns] == [
        "bom_view_revision_id",
        "parent_revision_id",
        "occurrence_key",
    ]
