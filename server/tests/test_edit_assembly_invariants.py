import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from modelryx.api.artifact_commits import _require_current_assembly_analysis
from modelryx.api.staging import StagingProposalUpdate


def _assembly_proposal(*, occurrence: str = "Bearing:1") -> StagingProposalUpdate:
    revision_id = uuid.uuid4()
    root_file_id = uuid.uuid4()
    return StagingProposalUpdate.model_validate(
        {
            "expected_snapshot_version": 7,
            "operation": "EDIT_ITEM_REVISION",
            "destination": {
                "item_id": str(uuid.uuid4()),
                "revision_id": str(revision_id),
                "base_snapshot_id": str(uuid.uuid4()),
            },
            "item": {"item_number": "ASM-1", "name": "Assembly", "item_type": "ASSEMBLY"},
            "revision": {"revision_code": "A", "cad_system": "Inventor", "native_format": "IAM"},
            "artifacts": [
                {
                    "client_artifact_id": str(uuid.uuid4()),
                    "artifact_type": "CAD_ASSEMBLY",
                    "name": "Assembly",
                    "classification": "AUTHORITATIVE",
                    "relation_type": "SPECIFICATION",
                    "is_primary": True,
                    "files": [
                        {
                            "staged_file_id": str(root_file_id),
                            "reference_name": "Assembly.iam",
                            "role": "PRIMARY",
                        }
                    ],
                }
            ],
            "bom": [
                {
                    "parent_revision_id": str(revision_id),
                    "child_revision_id": str(uuid.uuid4()),
                    "instance_name": occurrence,
                }
            ],
        }
    )


def _staging(*, analysis: dict | None, snapshot_version: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        snapshot_version=snapshot_version,
        result_json={} if analysis is None else {"assembly_analysis": analysis},
    )


def _analysis(*, occurrence: str = "Bearing:1", snapshot_version: int = 7) -> dict:
    return {
        "snapshot_version": snapshot_version,
        "package_staged": True,
        "report": {"root_logical_path": "Assembly.iam"},
        "reconciliation": [
            {
                "parent_logical_path": "Assembly.iam",
                "depth": 0,
                "occurrence_key": occurrence,
                "status": "UNCHANGED",
            }
        ],
        "identity_decisions": {},
    }


@pytest.mark.parametrize(
    "analysis",
    [None, _analysis(snapshot_version=6), {**_analysis(), "package_staged": False}],
)
def test_assembly_commit_requires_current_persisted_analysis(analysis: dict | None) -> None:
    with pytest.raises(HTTPException) as raised:
        _require_current_assembly_analysis(_staging(analysis=analysis), _assembly_proposal())
    assert raised.value.status_code == 409


def test_assembly_commit_rejects_bom_that_differs_from_analysis() -> None:
    with pytest.raises(HTTPException) as raised:
        _require_current_assembly_analysis(
            _staging(analysis=_analysis()),
            _assembly_proposal(occurrence="Different:1"),
        )
    assert raised.value.status_code == 409
    assert "proposed BOM differs" in str(raised.value.detail)


def test_assembly_commit_accepts_matching_analyzed_bom() -> None:
    _require_current_assembly_analysis(_staging(analysis=_analysis()), _assembly_proposal())
