import copy
import hashlib

import pytest
from modelryx.duplicate_detection import (
    Classification,
    DuplicateDetectionService,
    canonical_package_sha256,
)
from modelryx.duplicate_detection.reasoner import _compact
from modelryx.duplicate_detection.search_document import build_semantic_text


def metadata() -> dict:
    return {
        "identity": {"name": "MotorHousing", "kind": "part", "description": "Servo housing"},
        "geometry": {
            "bounding_box": {"x_mm": 120, "y_mm": 85, "z_mm": 45},
            "shape": {"volume_mm3": 400000, "surface_area_mm2": 30000},
            "topology": {"solid_count": 1, "face_count": 40, "edge_count": 80},
        },
        "physical": {
            "material": {"name": "Aluminum 6061", "normalized_name": "aluminum_6061"},
            "mass_kg": 1.08,
        },
        "features": {
            "summary": {"hole_count": 4, "fillet_count": 12, "chamfer_count": 4, "pocket_count": 2}
        },
        "quality": {
            "geometry": "complete",
            "physical": "complete",
            "features": "complete",
            "assembly": "not_applicable",
        },
    }


def test_package_hash_is_order_and_separator_independent() -> None:
    a, b = hashlib.sha256(b"a").hexdigest(), hashlib.sha256(b"b").hexdigest()
    assert canonical_package_sha256(
        [("dir\\a.step", a), ("b.json", b)]
    ) == canonical_package_sha256([("b.json", b), ("dir/a.step", a)])


def test_rotated_cross_format_part_is_engineering_duplicate() -> None:
    other = copy.deepcopy(metadata())
    other["geometry"]["bounding_box"] = {"x_mm": 45, "y_mm": 120, "z_mm": 85}
    other["identity"]["name"] = "motor_housing"
    assert (
        DuplicateDetectionService().compare(metadata(), other).classification
        == Classification.ENGINEERING_DUPLICATE
    )


def test_missing_step_features_are_omitted_not_scored_as_zero() -> None:
    other = copy.deepcopy(metadata())
    other.pop("features")
    other["quality"]["features"] = "unsupported"
    result = DuplicateDetectionService().compare(metadata(), other)
    assert result.scores.features is None
    assert result.classification == Classification.ENGINEERING_DUPLICATE


def test_dimensional_change_is_variant() -> None:
    other = copy.deepcopy(metadata())
    other["geometry"]["bounding_box"]["x_mm"] = 150
    other["geometry"]["shape"]["volume_mm3"] = 500000
    result = DuplicateDetectionService().compare(metadata(), other, embedding_score=0.95)
    assert result.classification == Classification.VARIANT


def test_semantic_document_excludes_operational_metadata() -> None:
    value = metadata()
    value["source"] = {"worker_version": "secret-worker"}
    value["native"] = {"object_id": "internal-id"}
    text = build_semantic_text(value)
    assert "MotorHousing" in text and "aluminum_6061" in text
    assert "secret-worker" not in text and "internal-id" not in text


def test_semantic_document_qualifies_unverified_array_geometry() -> None:
    value = metadata()
    value["native"] = {"link_arrays": [{"suppression_summary": {
        "unknown_inspected_count": 1, "uninspected_count": 0,
    }}]}
    text = build_semantic_text(value)
    assert "Array geometry or element activity is unverified" in text
    assert "Volume:" not in text and "Dimensions:" not in text and "Mass:" not in text
    assert "Material: aluminum_6061" in text
    value["native"]["link_arrays"][0]["suppression_summary"]["unknown_inspected_count"] = 0
    assert "Volume: approximately 400000" in build_semantic_text(value)


def test_duplicate_scoring_does_not_use_unverified_array_geometry_or_mass() -> None:
    source = metadata()
    candidate = copy.deepcopy(source)
    candidate["native"] = {"link_arrays": [{"suppression_summary": {
        "unknown_inspected_count": 1, "uninspected_count": 0,
    }}]}
    result = DuplicateDetectionService().compare(source, candidate)
    assert result.classification == Classification.INSUFFICIENT_DATA
    assert result.scores.geometry is None
    assert result.scores.physical is None
    assert any(
        "Array geometry or element activity is unverified" in reason for reason in result.reasons
    )
    assert (
        DuplicateDetectionService().compare(candidate, source).classification
        == Classification.INSUFFICIENT_DATA
    )
    compact = _compact(candidate)
    assert compact["bbox_sorted_mm"] is None and compact["volume_mm3"] is None
    assert compact["topology"] is None and compact["geometry_warning"]
    assert _compact(source)["volume_mm3"] == 400000


def test_semantic_document_includes_freecad_sketch_evidence() -> None:
    value = metadata()
    value["native"] = {
        "sketches": [
            {
                "name": "MountingSketch",
                "label": "Mounting profile",
                "geometry_count": 6,
                "construction_geometry_count": 2,
                "constraint_count": 9,
                "degrees_of_freedom": 3,
                "fully_constrained": False,
                "map_mode": "FlatFace",
                "constraints": [{"type": "Distance"}, {"type": "Coincident"}],
            }
        ]
    }
    text = build_semantic_text(value)
    assert "Sketch: Mounting profile" in text
    assert "degrees of freedom=3" in text
    assert "Coincident, Distance" in text


def test_semantic_document_includes_parameter_dependencies() -> None:
    value = metadata()
    value["native"] = {
        "parameter_graph": {
            "edges": [
                {
                    "source": "Parameters.Width",
                    "target": "Pad.Length",
                    "reference": "Parameters.Width",
                }
            ],
            "key_parameters": ["Parameters.Width"],
            "cycles": [],
        }
    }
    text = build_semantic_text(value)
    assert "Parameters.Width controls Pad.Length" in text
    assert "Key driving parameters: Parameters.Width" in text


def test_semantic_document_includes_native_feature_semantics() -> None:
    value = metadata()
    value["features"]["items"] = [
        {
            "name": "Shell001",
            "type": "SHELL",
            "properties": {
                "thickness_mm": 2.5,
                "base_refs": ["Body"],
                "reversed": True,
            },
        }
    ]
    text = build_semantic_text(value)
    assert "Feature: Shell001; type=SHELL" in text
    assert "thickness mm=2.5" in text
    assert "references=Body" in text


def test_semantic_document_includes_freecad_assembly_joints() -> None:
    value = metadata()
    value["native"] = {
        "assembly_joints": [
            {
                "name": "Pivot",
                "label": "Arm pivot",
                "type": "Revolute",
                "grounded": False,
                "suppressed": False,
                "degrees_of_freedom": 1,
                "references": [
                    {"role": "first", "object": "BasePlate"},
                    {"role": "second", "object": "Arm"},
                ],
            }
        ]
    }
    text = build_semantic_text(value)
    assert "Assembly joint: Arm pivot; type=Revolute" in text
    assert "degrees of freedom=1" in text
    assert "first=BasePlate, second=Arm" in text


def test_semantic_document_includes_freecad_configurations() -> None:
    value = metadata()
    value["native"] = {
        "configurations": {
            "active": "Machined",
            "active_values": ["Machined"],
            "variants": ["Default", "Lightweight", "Machined"],
            "selectors": [
                {
                    "object": "DesignVariants",
                    "label": "Design variants",
                    "property": "Configuration",
                    "active": "Machined",
                }
            ],
        }
    }
    text = build_semantic_text(value)
    assert "Active configurations: Machined" in text
    assert "Available configurations: Default, Lightweight, Machined" in text
    assert "Configuration selector: Design variants.Configuration=Machined" in text


def test_semantic_document_includes_freecad_reference_geometry() -> None:
    value = metadata()
    value["native"] = {
        "reference_geometry": [
            {
                "name": "MachiningLCS",
                "label": "Machining coordinate system",
                "kind": "coordinate_system",
                "map_mode": "FlatFace",
                "placement": {"position_mm": [10.0, 5.0, 0.0]},
                "support": [{"object": "TopFace", "subelements": ["Face1"]}],
            }
        ]
    }
    text = build_semantic_text(value)
    assert "Reference geometry: Machining coordinate system" in text
    assert "kind=coordinate_system" in text
    assert "position millimeters=[10.0, 5.0, 0.0]" in text
    assert "attached to=TopFace" in text


def test_invalid_or_duplicate_package_paths_are_rejected() -> None:
    digest = "0" * 64
    with pytest.raises(ValueError):
        canonical_package_sha256([("../a", digest)])
    with pytest.raises(ValueError):
        canonical_package_sha256([("A.step", digest), ("a.step", digest)])
