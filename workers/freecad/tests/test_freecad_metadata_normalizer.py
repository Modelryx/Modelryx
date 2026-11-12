import importlib.util
from pathlib import Path

import pytest


def load_normalizer_module():
    path = Path(__file__).resolve().parents[1] / "converter/part_metadata_normalizer.py"
    spec = importlib.util.spec_from_file_location("part_metadata_normalizer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalizes_freecad_raw_metadata_without_mutating_it() -> None:
    module = load_normalizer_module()
    raw = {
        "document_label": "MotorHousing",
        "freecad_version": "1.0.2",
        "worker_version": "0.3.0",
        "extracted_at": "2026-08-20T22:30:00Z",
        "bounding_box": {"x": 120.0, "y": 85.0, "z": 45.0},
        "shape_volume": 400000.0,
        "shape_area": 148000.0,
        "topology": {"solid_count": 1, "face_count": 74, "edge_count": 156},
        "center": {"x": -2.0, "y": 0.0, "z": 22.5},
        "body": "Body",
        "tip": "Fillet003",
        "quality": {"physical": "unavailable", "warnings": ["No material assigned."]},
    }
    original = dict(raw)

    result = module.normalize_freecad_metadata(raw)

    assert raw == original
    assert result["schema"] == "modelryx.part-metadata"
    assert result["schema_version"] == "1.0"
    assert result["source"] == {
        "system": "freecad",
        "system_version": "1.0.2",
        "worker": "modelryx-freecad-worker",
        "worker_version": "0.3.0",
        "extracted_at": "2026-08-20T22:30:00Z",
    }
    assert result["identity"] == {"name": "MotorHousing", "kind": "part"}
    assert result["geometry"]["bounding_box"] == {
        "x_mm": 120.0,
        "y_mm": 85.0,
        "z_mm": 45.0,
    }
    assert result["geometry"]["shape"]["volume_mm3"] == 400000.0
    assert result["geometry"]["center"]["x_mm"] == -2.0
    assert result["native"] == {
        "body": "Body",
        "tip": "Fillet003",
        "document_label": "MotorHousing",
    }
    assert result["quality"]["features"] == "unsupported"


def test_preserves_fcstd_document_provenance_in_native_metadata() -> None:
    module = load_normalizer_module()
    provenance = {
        "format": "FCStd",
        "file_format_version": "1",
        "document_schema_version": "4",
        "authoring_freecad_version": "1.1.0",
        "version_attributes": {"FileVersion": "1"},
    }

    result = module.normalize_freecad_metadata(
        {
            "name": "Versioned part",
            "bounding_box": {"x": 1, "y": 1, "z": 1},
            "shape_volume": 1,
            "shape_area": 1,
            "source_document": provenance,
        }
    )

    assert result["native"]["source_document"] == provenance


def test_preserves_native_occurrence_evidence_and_assembly_counts() -> None:
    module = load_normalizer_module()
    occurrences = [
        {
            "partId": "part-1",
            "occurrenceId": "occurrence-1",
            "parentOccurrenceId": "root",
            "nodeType": "part",
            "cadType": "Feature",
            "name": "Bracket",
            "source": {"objectName": "Bracket001", "typeId": "Part::Feature"},
            "placement": {
                "position": [0.01, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            },
            "properties": {"StockCode": "BRK-001", "Length": "20.00 mm"},
        }
    ]

    result = module.normalize_freecad_metadata(
        {
            "name": "Assembly",
            "kind": "assembly",
            "bounding_box": {"x": 20, "y": 10, "z": 5},
            "shape_volume": 1000,
            "shape_area": 700,
            "assembly": {
                "is_assembly": True,
                "instance_count": 1,
                "unique_component_count": 1,
                "subassembly_count": 0,
                "bom_row_count": 1,
            },
            "native": {"occurrences": occurrences},
        }
    )

    assert result["assembly"]["unique_component_count"] == 1
    assert result["assembly"]["bom_row_count"] == 1
    assert result["native"]["occurrences"] == occurrences


def test_maps_common_freecad_properties_to_identity_without_losing_them() -> None:
    module = load_normalizer_module()
    properties = {
        "Part Number": " BRK-1042 ",
        "RevisionNumber": "C",
        "Description": "Motor mounting bracket",
        "Classification": "mounting; fabricated",
        "Keywords": ["motor", "bracket"],
        "SupplierCode": "ACME",
    }

    result = module.normalize_freecad_metadata(
        {
            "name": "Bracket",
            "bounding_box": {"x": 20, "y": 10, "z": 5},
            "shape_volume": 1000,
            "shape_area": 700,
            "properties": properties,
        }
    )

    assert result["identity"] == {
        "name": "Bracket",
        "kind": "part",
        "description": "Motor mounting bracket",
        "part_number": "BRK-1042",
        "revision": "C",
        "classification": ["mounting", "fabricated"],
        "tags": ["motor", "bracket"],
    }
    assert result["properties"] == properties


def test_explicit_identity_wins_over_property_aliases() -> None:
    module = load_normalizer_module()
    result = module.normalize_freecad_metadata(
        {
            "identity": {"name": "Bracket", "part_number": "MANAGED-1"},
            "properties": {"PartNumber": "FC-1"},
            "bounding_box": {"x": 20, "y": 10, "z": 5},
            "shape_volume": 1000,
            "shape_area": 700,
        }
    )

    assert result["identity"]["part_number"] == "MANAGED-1"


def test_preserves_enriched_partmetadata_contract_fields() -> None:
    module = load_normalizer_module()
    result = module.normalize_freecad_metadata(
        {
            "name": "Drilled bracket",
            "kind": "assembly",
            "bounding_box": {"x": 50, "y": 20, "z": 10},
            "shape_volume": 8000,
            "shape_area": 4200,
            "center": {"x": 25, "y": 10, "z": 5},
            "physical": {
                "material": {"name": "Steel"},
                "density_kg_m3": 7850,
                "mass_kg": 0.0628,
                "center_of_mass": {"x_mm": 25, "y_mm": 10, "z_mm": 5},
            },
            "features": {
                "source": "history",
                "summary": {"hole_count": 2},
                "holes": [{"diameter_mm": 6, "through": True, "count": 2}],
            },
            "assembly": {
                "is_assembly": True,
                "instance_count": 2,
                "unique_component_count": 1,
                "subassembly_count": 0,
                "bom_row_count": 1,
            },
            "native": {
                "occurrences": [{"occurrenceId": "occ-1", "partId": "part-1"}],
                "bom_rows": [{"stable_id": "part-1", "quantity": 2}],
            },
            "quality": {
                "geometry": "complete",
                "physical": "complete",
                "features": "complete",
                "assembly": "complete",
                "warnings": [],
            },
        }
    )
    assert result["schema"] == "modelryx.part-metadata"
    assert result["identity"]["kind"] == "assembly"
    assert result["physical"]["material"] == {"name": "Steel"}
    assert result["physical"]["mass_kg"] == pytest.approx(0.0628)
    assert result["features"]["summary"]["hole_count"] == 2
    assert result["features"]["holes"] == [{"diameter_mm": 6, "through": True, "count": 2}]
    assert result["assembly"]["instance_count"] == 2
    assert result["native"]["occurrences"][0]["occurrenceId"] == "occ-1"


def test_preserves_ai_extraction_extensions() -> None:
    extraction = {
        "sections": {},
        "capabilities": {"feature_history": "complete"},
        "provenance": {
            "source_system": "freecad",
            "worker": "modelryx-freecad-worker",
            "worker_version": "1",
            "method": "native",
        },
    }
    result = load_normalizer_module().normalize_freecad_metadata(
        {
            "name": "Configured plate",
            "bounding_box": {"x": 50, "y": 20, "z": 10},
            "shape_volume": 8000,
            "shape_area": 4200,
            "parameters": [{"name": "Thickness", "value": 10, "is_key": True, "is_user": True}],
            "manufacturing": {"process": "machining", "stock_form": "plate"},
            "extraction": extraction,
        }
    )

    assert result["parameters"][0]["name"] == "Thickness"
    assert result["manufacturing"]["stock_form"] == "plate"
    assert result["extraction"] == extraction


def test_preserves_mixed_model_and_drawing_metadata() -> None:
    module = load_normalizer_module()
    sheets = [
        {
            "stable_id": "Page",
            "name": "Page",
            "page": 1,
            "width_mm": 420.0,
            "height_mm": 297.0,
            "view_count": 3,
        }
    ]
    drawings = [
        {
            "stable_id": "freecad-techdraw",
            "filename": "drawing-previews.json",
            "mime_type": "application/json",
            "page_count": 1,
            "sections": [],
            "entities": [],
        }
    ]

    result = module.normalize_freecad_metadata(
        {
            "name": "Model and drawing",
            "bounding_box": {"x": 10, "y": 20, "z": 30},
            "shape_volume": 100,
            "shape_area": 200,
            "drawings": drawings,
            "native": {"drawing_sheets": sheets},
        }
    )

    assert result["drawings"] == drawings
    assert result["native"]["drawing_sheets"] == sheets


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"shape_volume": -1}, "volume_mm3 must be non-negative"),
        ({"bounding_box": {"x": 1, "y": 2}}, "requires x, y, and z"),
        ({"quality": {"geometry": "good"}}, "quality.geometry must be one of"),
    ],
)
def test_rejects_invalid_engineering_values(change: dict, message: str) -> None:
    module = load_normalizer_module()
    raw = {
        "name": "Bracket",
        "bounding_box": {"x": 10, "y": 20, "z": 30},
        "shape_volume": 100,
        "shape_area": 200,
        **change,
    }
    with pytest.raises(module.PartMetadataNormalizationError, match=message):
        module.normalize_freecad_metadata(raw)


def test_preserves_model_axis_order_and_normalizes_feature_counts() -> None:
    module = load_normalizer_module()
    result = module.PartMetadataNormalizer(worker_version="1.2.3").normalize(
        {
            "identity": {"name": "Plate", "kind": "part", "tags": ["steel"]},
            "geometry": {
                "bounding_box": {"x_mm": 10, "y_mm": 100, "z_mm": 50},
                "shape": {"volume_mm3": 1000, "surface_area_mm2": 500},
            },
            "features": {"source": "history", "summary": {"hole_count": 4}},
            "quality": {"features": "complete"},
        }
    )
    assert list(result["geometry"]["bounding_box"].values()) == [10, 100, 50]
    assert result["features"] == {"source": "history", "summary": {"hole_count": 4}}
    assert result["identity"]["tags"] == ["steel"]


@pytest.mark.parametrize(
    ("type_id", "expected"),
    [
        ("PartDesign::Hole", "hole"),
        ("PartDesign::Pad", "extrusion"),
        ("PartDesign::AdditiveBox", "boss"),
        ("PartDesign::SubtractiveCylinder", "pocket"),
        ("PartDesign::SubtractivePrism", "pocket"),
        ("PartDesign::AdditiveWedge", "boss"),
        ("PartDesign::SubtractiveEllipsoid", "pocket"),
        ("PartDesign::AdditiveHelix", "sweep"),
        ("PartDesign::SubtractiveHelix", "sweep"),
        ("PartDesign::Revolution", "revolution"),
        ("PartDesign::Pocket", "pocket"),
        ("PartDesign::AdditiveLoft", "loft"),
        ("PartDesign::SubtractivePipe", "sweep"),
        ("PartDesign::Mirrored", "pattern"),
        ("PartDesign::Thickness", "shell"),
        ("PartDesign::Boolean", "boolean"),
        ("Part::Extrusion", "extrusion"),
        ("Part::Box", "boss"),
        ("Part::Cylinder", "boss"),
        ("Part::Prism", "boss"),
        ("Part::Wedge", "boss"),
        ("Part::Ellipsoid", "boss"),
        ("Part::Cut", "boolean"),
        ("Part::Loft", "loft"),
        ("Sketcher::SketchObject", None),
    ],
)
def test_maps_freecad_history_to_engineering_features(type_id: str, expected: str | None) -> None:
    module = load_normalizer_module()
    assert module.normalized_freecad_feature_name(type_id) == expected


def test_maps_draft_array_proxy_to_pattern_without_classifying_generic_feature_python() -> None:
    module = load_normalizer_module()

    assert module.normalized_freecad_feature_name("Part::FeaturePython", "Array") == "pattern"
    assert module.normalized_freecad_feature_name("Part::FeaturePython", "PathArray") == "pattern"
    assert module.normalized_freecad_feature_name("Part::FeaturePython", "PointArray") == "pattern"
    assert module.normalized_freecad_feature_name("Part::FeaturePython", "CustomFeature") is None


@pytest.mark.parametrize(
    "type_id", ["PartDesign::PocketHelper", "PartDesign::HoleReference", "Other::Pad"]
)
def test_unknown_types_do_not_inherit_feature_semantics_from_their_names(type_id: str) -> None:
    assert load_normalizer_module().normalized_freecad_feature_name(type_id) is None


@pytest.mark.parametrize(
    ("type_id", "expected"),
    [
        ("PartDesign::Plane", "plane"),
        ("PartDesign::Line", "axis"),
        ("PartDesign::Point", "point"),
        ("PartDesign::CoordinateSystem", "coordinate_system"),
        ("PartDesign::ShapeBinder", "shape_binder"),
        ("PartDesign::SubShapeBinder", "shape_binder"),
        ("PartDesign::OriginFeature", "origin"),
        ("PartDesign::Pad", None),
    ],
)
def test_classifies_reference_geometry_outside_feature_history(
    type_id: str, expected: str | None
) -> None:
    module = load_normalizer_module()

    assert module.freecad_reference_geometry_kind(type_id) == expected


def test_counts_circular_pocket_profiles_as_physical_holes() -> None:
    module = load_normalizer_module()

    assert module.normalized_freecad_feature_names(
        "PartDesign::Pocket", ("Part::GeomCircle",), closed_profile_count=1
    ) == ["hole"]
    assert module.normalized_freecad_feature_names(
        "PartDesign::Pocket", ("Part::GeomCircle",) * 4, closed_profile_count=4
    ) == ["pocket"]
    assert module.normalized_freecad_feature_names(
        "PartDesign::Pocket",
        ("Part::GeomLineSegment",) * 4,
    ) == ["pocket"]


def test_counts_native_hole_profile_occurrences() -> None:
    module = load_normalizer_module()

    assert (
        module.normalized_freecad_feature_names(
            "PartDesign::Hole",
            ("Part::GeomPoint", "Part::GeomCircle") * 4,
        )
        == ["hole"] * 4
    )
    assert (
        module.normalized_freecad_feature_names(
            "PartDesign::Hole",
            ("Part::GeomPoint",) * 3,
        )
        == ["hole"] * 3
    )


def test_counts_separate_closed_pocket_profiles() -> None:
    module = load_normalizer_module()

    assert (
        module.normalized_freecad_feature_names(
            "PartDesign::Pocket",
            ("Part::GeomLineSegment",) * 16,
            closed_profile_count=4,
        )
        == ["pocket"] * 4
    )
    assert module.normalized_freecad_feature_names(
        "PartDesign::Pocket",
        ("Part::GeomCircle", "Part::GeomLineSegment", "Part::GeomLineSegment"),
        closed_profile_count=2,
    ) == ["pocket"]


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        ({"Suppressed": True}, True),
        ({"is_suppressed": False}, False),
        ({"Visibility": False}, None),
        ({"Suppressed": "true"}, None),
        ({}, None),
    ],
)
def test_reads_only_explicit_boolean_feature_suppression(
    properties: dict[str, object], expected: bool | None
) -> None:
    module = load_normalizer_module()

    assert module.freecad_feature_suppressed(properties) is expected


def test_suppressed_features_do_not_contribute_effective_feature_counts() -> None:
    module = load_normalizer_module()

    assert (
        module.normalized_freecad_feature_names(
            "PartDesign::Pocket",
            ("Part::GeomCircle",),
            closed_profile_count=1,
            suppressed=True,
        )
        == []
    )
