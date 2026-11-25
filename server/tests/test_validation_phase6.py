import uuid

import pytest
from modelryx.models import ValidationFinding, ValidationRun
from modelryx.validation.models import AIOutput, FindingInput
from modelryx.validation.service import (
    SUPPORTED_CAD_EXTENSIONS,
    ValidationService,
    can_acknowledge,
    geometry_findings,
    metadata_quality_findings,
    resolve_status,
    similarity_evaluable,
    summarize,
    validator_state,
)


def test_supported_cad_extensions_include_builtin_formats() -> None:
    assert {".step", ".stp", ".iges", ".igs"} <= SUPPORTED_CAD_EXTENSIONS
    # Native extensions are added from enabled worker capabilities at validation time.
    assert ".fcstd" not in SUPPORTED_CAD_EXTENSIONS


def finding(*, severity: str = "WARNING", blocking: bool = False) -> FindingInput:
    return FindingInput(
        code="MATERIAL_MISSING",
        category="metadata",
        severity=severity,
        blocking=blocking,
        title="Material missing",
        message="No material was supplied.",
        source={"validator": "test", "version": "1"},
        actions=["set_material"],
    )


def test_policy_never_lets_warnings_override_a_blocker() -> None:
    values = [finding(), finding(severity="CRITICAL", blocking=True)]
    assert resolve_status(values) == "BLOCKED"
    assert summarize(values) == {
        "critical": 1,
        "error": 0,
        "warning": 1,
        "info": 0,
        "blocking": 1,
        "total": 2,
    }


def test_policy_ready_states() -> None:
    assert resolve_status([]) == "READY"
    assert resolve_status([finding()]) == "READY_WITH_WARNINGS"


def test_validator_state_reports_warning_failure_and_not_evaluated() -> None:
    assert validator_state([finding()], {"metadata"}) == "WARNING"
    assert validator_state([finding(severity="ERROR", blocking=True)], {"metadata"}) == "FAIL"
    assert validator_state([], {"geometry"}, evaluated=False) == "NOT_EVALUATED"


def test_only_explicit_non_blocking_warnings_can_be_acknowledged() -> None:
    acknowledgeable = finding()
    acknowledgeable.actions.append("ignore_warning")
    assert can_acknowledge(acknowledgeable)
    assert not can_acknowledge(finding())
    assert not can_acknowledge(finding(severity="ERROR", blocking=True))


def test_geometry_rules_report_exact_invalid_evidence() -> None:
    findings = geometry_findings(
        {
            "geometry": {
                "bounding_box": {"x_mm": 10, "y_mm": 0, "z_mm": 3},
                "shape": {"volume_mm3": 25, "surface_area_mm2": 0},
                "topology": {"solid_count": 0},
            }
        }
    )
    assert [item.code for item in findings] == [
        "GEOMETRY_BBOX_INVALID",
        "GEOMETRY_INVALID",
        "GEOMETRY_INVALID",
    ]
    assert all(item.blocking for item in findings)
    assert findings[0].details["bounding_box"]["y_mm"] == 0


def test_geometry_rules_accept_positive_consistent_metrics() -> None:
    assert (
        geometry_findings(
            {
                "geometry": {
                    "bounding_box": {"x_mm": 10, "y_mm": 5, "z_mm": 3},
                    "shape": {"volume_mm3": 25, "surface_area_mm2": 50},
                    "topology": {"solid_count": 1},
                }
            }
        )
        == []
    )


def test_drawing_without_solid_geometry_does_not_block_validation() -> None:
    metadata = {
        "native": {"document_type": "drawing"},
        "quality": {"geometry": "not_applicable"},
        "geometry": {
            "bounding_box": {"x_mm": 0, "y_mm": 0, "z_mm": 0},
            "shape": {"volume_mm3": 0, "surface_area_mm2": 0},
        },
    }
    assert geometry_findings(metadata) == []
    metadata["native"]["document_type"] = "part"
    assert any(finding.blocking for finding in geometry_findings(metadata))


def test_unverified_array_geometry_warns_instead_of_claiming_pass_or_failure() -> None:
    value = {
        "geometry": {
            "bounding_box": {"x_mm": 0, "y_mm": 0, "z_mm": 0},
            "shape": {"volume_mm3": 0, "surface_area_mm2": 0},
        },
        "native": {"extraction_diagnostics": [{"code": "LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN"}]},
    }
    findings = geometry_findings(value)
    assert [item.code for item in findings] == ["ARRAY_GEOMETRY_UNVERIFIED"]
    assert not findings[0].blocking
    assert validator_state(findings, {"geometry"}) == "WARNING"
    value["geometry"]["shape"]["volume_mm3"] = 10
    assert geometry_findings(value)[0].code == "ARRAY_GEOMETRY_UNVERIFIED"


def test_point_and_curve_only_part_is_a_non_blocking_limitation() -> None:
    findings = geometry_findings(
        {
            "geometry": {
                "bounding_box": {"x_mm": 0, "y_mm": 0, "z_mm": 0},
                "shape": {"volume_mm3": 0, "surface_area_mm2": 0},
            },
            "quality": {
                "warnings": [
                    "This part contains points and curves but no surface or solid bodies; "
                    "physical properties are unavailable; the 3D preview contains "
                    "reference points and curves."
                ]
            },
        }
    )
    assert [item.code for item in findings] == ["BODY_GEOMETRY_UNAVAILABLE"]
    assert findings[0].severity == "WARNING"
    assert findings[0].blocking is False


def test_surface_only_model_does_not_require_solid_volume() -> None:
    metadata = {
        "geometry": {
            "bounding_box": {"x_mm": 10, "y_mm": 20, "z_mm": 30},
            "shape": {"volume_mm3": 0, "surface_area_mm2": 100},
        },
        "quality": {"warnings": ["Surface-only model; no solid bodies were found."]},
    }
    assert not any(f.blocking for f in geometry_findings(metadata))
    metadata["geometry"]["shape"]["surface_area_mm2"] = 0
    assert any(f.blocking for f in geometry_findings(metadata))
    metadata["quality"]["warnings"] = []
    assert any(f.code == "GEOMETRY_ZERO_VOLUME" for f in geometry_findings(metadata))


def test_similarity_is_suppressed_only_by_blocking_metadata_or_geometry() -> None:
    missing = FindingInput(
        code="METADATA_MISSING",
        category="metadata",
        severity="ERROR",
        blocking=True,
        title="Missing metadata",
        message="Metadata is unavailable.",
        source={"validator": "test"},
    )
    assert not similarity_evaluable([missing])
    assert similarity_evaluable([finding()])
    assert similarity_evaluable(
        [
            FindingInput(
                code="FILE_EMPTY",
                category="file",
                severity="ERROR",
                blocking=True,
                title="Empty file",
                message="File is empty.",
                source={"validator": "test"},
            )
        ]
    )


def test_metadata_quality_preserves_worker_evidence_without_inventing_defects() -> None:
    findings = metadata_quality_findings(
        {
            "identity": {"kind": "assembly"},
            "quality": {
                "geometry": "complete",
                "physical": "unavailable",
                "features": "unsupported",
                "assembly": "partial",
                "warnings": ["Material was not available from source."],
            },
        }
    )
    assert [item.code for item in findings] == [
        "MASS_UNAVAILABLE",
        "METADATA_QUALITY_INCOMPLETE",
        "WORKER_REPORTED_WARNING",
    ]
    assert findings[1].details["domains"] == {
        "physical": "unavailable",
        "assembly": "partial",
    }
    assert findings[2].evidence["worker_message"] == ("Material was not available from source.")
    assert not any(item.blocking for item in findings)


def test_unsupported_optional_features_are_not_reported_as_a_defect() -> None:
    findings = metadata_quality_findings(
        {
            "identity": {"kind": "part"},
            "physical": {"mass_kg": 0.4},
            "quality": {
                "geometry": "complete",
                "physical": "complete",
                "features": "unsupported",
                "assembly": "not_applicable",
                "warnings": [],
            },
        }
    )
    assert findings == []


def test_ai_grounding_rejects_invented_actions() -> None:
    finding_id = uuid.uuid4()
    stored = ValidationFinding(
        id=finding_id,
        validation_run_id=uuid.uuid4(),
        code="MATERIAL_MISSING",
        category="metadata",
        severity="WARNING",
        blocking=False,
        title="Material missing",
        message="No material was supplied.",
        source_json={},
        location_json={},
        details_json={},
        evidence_json={},
        actions_json=["set_material"],
    )
    output = AIOutput.model_validate(
        {
            "headline": "Review material",
            "summary": "One warning.",
            "issues": [
                {
                    "finding_id": str(finding_id),
                    "explanation": "Material is absent.",
                    "impact": "Search quality may be reduced.",
                    "recommended_actions": [{"action": "delete_item", "label": "Delete"}],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="invented"):
        ValidationService._validate_grounding(output, [stored])


def test_deterministic_fallback_preserves_blocking_fact() -> None:
    run = ValidationRun(status="BLOCKED", summary_json={"total": 1, "blocking": 1})
    stored = ValidationFinding(
        id=uuid.uuid4(),
        code="FILE_EMPTY",
        category="file",
        severity="ERROR",
        blocking=True,
        title="Empty file",
        message="The file contains no data.",
        actions_json=["replace_file"],
    )
    output = ValidationService._fallback(run, [stored])
    assert output.headline == "Validation blocked"
    assert output.issues[0].impact == "This finding blocks promotion."
    assert output.issues[0].recommended_actions[0].action == "replace_file"


def test_validation_history_is_newest_first_and_bounded() -> None:
    class ScalarResult:
        def __init__(self) -> None:
            self.values = [
                ValidationRun(run_number=3),
                ValidationRun(run_number=2),
            ]

        def __iter__(self):
            return iter(self.values)

    class FakeDb:
        def scalars(self, _statement):
            return ScalarResult()

    service = ValidationService.__new__(ValidationService)
    service.db = FakeDb()
    history = service.history(uuid.uuid4(), limit=2)
    assert [run.run_number for run in history] == [3, 2]
