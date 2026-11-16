from copy import deepcopy

import pytest
from modelryx.metadata import (
    MetadataSchemaError,
    validate_engineering_metadata,
    validate_part_metadata,
)
from modelryx.metadata.service import (
    MetadataService,
    add_drawing_identity_warnings,
    canonicalize_item_kind,
    enhance_extraction_metadata,
)


def valid_metadata() -> dict:
    return {
        "schema": "modelryx.part-metadata",
        "schema_version": "1.0",
        "source": {
            "system": "freecad",
            "system_version": "1.0.2",
            "worker": "modelryx-freecad-worker",
            "worker_version": "modelryx-fcstd-5",
            "extracted_at": "2026-08-21T12:00:00Z",
        },
        "identity": {"name": "Bracket", "kind": "part"},
        "geometry": {
            "bounding_box": {"x_mm": 120, "y_mm": 80, "z_mm": 12},
            "shape": {"volume_mm3": 45000, "surface_area_mm2": 22000},
            "topology": {"solid_count": 1, "face_count": 14},
        },
        "assembly": {"is_assembly": False},
        "quality": {
            "geometry": "complete",
            "physical": "unavailable",
            "features": "unsupported",
            "assembly": "not_applicable",
            "warnings": ["Material was not available from source."],
        },
    }


def test_accepts_part_metadata_v1() -> None:
    metadata = valid_metadata()
    assert validate_part_metadata(metadata) is metadata


def test_accepts_optional_metadata_quality_and_rejects_invalid_quality() -> None:
    metadata = valid_metadata()
    metadata["source"]["system"] = "example_cad"
    metadata["source"]["system_version"] = "2506.0.0.0"
    metadata["quality"]["metadata"] = "partial"
    assert validate_part_metadata(metadata) is metadata
    metadata["quality"]["physical"] = "complete"
    metadata["quality"]["features"] = "complete"
    assert MetadataService.status_for(metadata) == "PARTIAL"
    metadata["quality"]["metadata"] = "unknown"
    with pytest.raises(MetadataSchemaError, match="quality.metadata"):
        validate_part_metadata(metadata)


def test_accepts_external_assembly_occurrences_and_groups() -> None:
    metadata = valid_metadata()
    metadata["source"]["system"] = "example_cad"
    metadata["identity"]["kind"] = "assembly"
    component = {
        "occurrence_id": "occurrence:1",
        "name": "Nut",
        "quantity": 1,
        "native_prototype_id": "prototype:nut",
        "reference_set": "MODEL",
        "occurrence_order": 0,
        "visible": True,
        "reference": None,
        "substitute": None,
        "representation_mode": "exact",
        "position_status": "positioned",
        "load_diagnostic": "loaded",
    }
    metadata["assembly"] = {
        "is_assembly": True,
        "components": [component, {**component, "occurrence_id": "occurrence:2"}],
        "component_groups": [{
            "parent_occurrence_id": None,
            "native_prototype_id": "prototype:nut",
            "revision": "A",
            "quantity": 2,
            "occurrence_ids": ["occurrence:1", "occurrence:2"],
            "reference": None,
            "substitute": None,
        }],
    }
    assert validate_part_metadata(metadata) is metadata
    group = metadata["assembly"]["component_groups"][0]
    group["occurrence_ids"] = ["occurrence:1", "occurrence:1"]
    with pytest.raises(MetadataSchemaError, match="occurrence_ids"):
        validate_part_metadata(metadata)
    group["occurrence_ids"] = ["occurrence:1", "occurrence:2"]
    group["unexpected"] = True
    with pytest.raises(MetadataSchemaError, match="component_groups"):
        validate_part_metadata(metadata)


@pytest.mark.parametrize("source_system", ["freecad", "example_cad", "example_cad"])
def test_enrichment_exposes_searchable_extraction_evidence_for_every_cad_path(
    source_system: str,
) -> None:
    metadata = valid_metadata()
    metadata["source"]["system"] = source_system
    metadata["identity"]["kind"] = "assembly"
    metadata["properties"] = {
        "manufacturing_process": "machining",
        "stock_form": "plate",
        "finish": "anodized",
        "configuration": "Production",
    }
    metadata["physical"] = {"material": {"name": "Aluminum 6061-T6"}}
    metadata["native"] = {
        "parameters": [
            {"name": "Thickness", "value": 12.5, "unit": "mm", "is_key": True},
            {"name": "Description", "value": "not numeric", "expression": "2 * Thickness"},
        ],
        "occurrences": [
            {
                "id": "root/plate:1",
                "name": "Plate",
                "quantity": "2",
                "part_number": "P-1",
                "configuration": "Long",
                "dependency": {
                    "status": "resolved",
                    "document": "plate.FCStd",
                    "external": True,
                },
            },
            {"id": "root/bad:1", "name": "Bad quantity", "quantity": "invalid"},
        ],
    }
    metadata["assembly"] = {"is_assembly": True, "instance_count": 3}
    metadata["quality"].update({"physical": "complete", "assembly": "complete"})

    enriched = enhance_extraction_metadata(metadata)

    assert enriched["physical"]["material"] == {
        "name": "Aluminum 6061-T6",
        "normalized_name": "aluminum 6061-t6",
        "family": "Aluminum",
        "grade": "6061-T6",
        "temper": "T6",
    }
    assert enriched["manufacturing"]["process"] == "machining"
    assert enriched["parameters"][0]["value"] == 12.5
    assert enriched["parameters"][1]["value"] is None
    assert enriched["assembly"]["components"][0]["quantity"] == 2
    assert enriched["assembly"]["components"][0]["dependency_status"] == "resolved"
    assert enriched["assembly"]["components"][0]["dependency_document"] == "plate.FCStd"
    assert enriched["assembly"]["components"][1]["quantity"] == 1
    assert enriched["extraction"]["provenance"]["source_system"] == source_system
    assert enriched["extraction"]["sections"]["parameters"]["records_found"] == 2
    assert validate_part_metadata(enriched) is enriched


def test_enrichment_preserves_worker_supplied_evidence() -> None:
    metadata = valid_metadata()
    metadata["extraction"] = {
        "sections": {
            "features": {
                "status": "partial",
                "records_found": 4,
                "records_skipped": 2,
                "method": "adapter_history_walk",
                "confidence": 0.65,
            }
        },
        "capabilities": {"gdt": "unsupported"},
        "provenance": {
            "source_system": "freecad",
            "worker": "modelryx-freecad-worker",
            "worker_version": "modelryx-fcstd-5",
            "method": "native",
            "confidence": 0.65,
            "section_confidence": {"features": 0.65},
        },
    }

    enriched = enhance_extraction_metadata(metadata)

    assert enriched["extraction"]["sections"]["features"]["records_skipped"] == 2
    assert enriched["extraction"]["capabilities"]["gdt"] == "unsupported"
    assert enriched["extraction"]["provenance"]["confidence"] == 0.65
    assert enriched["extraction"]["provenance"]["section_confidence"] == {"features": 0.65}
    validate_part_metadata(enriched)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_rejects_out_of_range_section_confidence(confidence: float) -> None:
    metadata = valid_metadata()
    metadata["extraction"] = {
        "sections": {},
        "capabilities": {},
        "provenance": {
            "source_system": "freecad",
            "worker": "worker",
            "worker_version": "1",
            "method": "native",
            "section_confidence": {"geometry": confidence},
        },
    }
    with pytest.raises(MetadataSchemaError, match="section_confidence"):
        validate_part_metadata(metadata)


def test_managed_part_type_overrides_converter_assembly_guess() -> None:
    metadata = valid_metadata()
    metadata["identity"]["kind"] = "assembly"
    metadata["assembly"] = {
        "is_assembly": True,
        "instance_count": 2,
        "unique_component_count": 1,
        "subassembly_count": 1,
    }
    metadata["quality"]["assembly"] = "complete"

    canonical = canonicalize_item_kind(metadata, "PART")

    assert canonical["identity"]["kind"] == "part"
    assert canonical["assembly"] == {"is_assembly": False}
    assert canonical["quality"]["assembly"] == "not_applicable"
    assert metadata["identity"]["kind"] == "assembly"
    validate_part_metadata(canonical)
    validate_engineering_metadata(canonical)


def test_drawing_title_block_mismatches_add_managed_identity_warnings() -> None:
    metadata = valid_metadata()
    metadata["identity"].update({"part_number": "DWG-100", "revision": "B"})
    metadata["native"] = {"title_block": {"PART_NO": "DWG-100", "REV": "B"}}

    checked = add_drawing_identity_warnings(
        metadata,
        type("Item", (), {"part_number": "PF-100"})(),
        type("Revision", (), {"revision_code": "A"})(),
    )

    assert checked["quality"]["warnings"][-2:] == [
        "Drawing title-block part number 'DWG-100' does not match Modelryx item 'PF-100'.",
        "Drawing title-block revision 'B' does not match Modelryx revision 'A'.",
    ]
    assert checked["properties"]["title_block_validation_status"] == "mismatch"
    assert len(checked["properties"]["title_block_validation_issues"]) == 2
    assert metadata["quality"]["warnings"] == ["Material was not available from source."]
    validate_part_metadata(checked)


def test_drawing_title_block_matching_is_case_and_whitespace_insensitive() -> None:
    metadata = valid_metadata()
    metadata["identity"].update({"part_number": " pf-100 ", "revision": "a"})
    metadata["native"] = {"title_block": {"PART_NO": " pf-100 ", "REV": "a"}}

    checked = add_drawing_identity_warnings(
        metadata,
        type("Item", (), {"part_number": "PF-100"})(),
        type("Revision", (), {"revision_code": "A"})(),
    )

    assert checked["quality"]["warnings"] == ["Material was not available from source."]
    assert checked["properties"]["title_block_validation_status"] == "matched"


def test_drawing_title_block_missing_managed_fields_is_incomplete() -> None:
    metadata = valid_metadata()
    metadata["native"] = {"title_block": {"DRAWING_NO": "D-100"}}

    checked = add_drawing_identity_warnings(
        metadata,
        type("Item", (), {"part_number": "PF-100"})(),
        type("Revision", (), {"revision_code": "A"})(),
    )

    assert checked["properties"]["title_block_validation_status"] == "incomplete"
    assert checked["properties"]["title_block_validation_issues"] == [
        "Drawing title block does not provide a mapped part number.",
        "Drawing title block does not provide a mapped revision.",
    ]
    assert (
        checked["quality"]["warnings"][-2:]
        == checked["properties"]["title_block_validation_issues"]
    )
    validate_part_metadata(checked)


def test_accepts_normalized_feature_items() -> None:
    metadata = valid_metadata()
    metadata["features"] = {
        "source": "history",
        "summary": {"hole_count": 1},
        "items": [
            {
                "persistent_id": "Hole001",
                "name": "Mounting hole",
                "type": "HOLE",
                "properties": {"Diameter": 8.0, "Tags": ["mounting", "through"]},
            }
        ],
    }
    metadata["quality"]["features"] = "complete"

    assert validate_part_metadata(metadata) is metadata


def test_accepts_empty_native_feature_property_array() -> None:
    """Empty arrays have no element type and must not fail the property-value union."""
    metadata = valid_metadata()
    metadata["features"] = {
        "source": "history",
        "summary": {},
        "items": [
            {
                "persistent_id": "Body",
                "name": "Body",
                "type": "BODY",
                "properties": {"ExpressionEngine": []},
            }
        ],
    }

    assert validate_part_metadata(metadata) is metadata


def test_rejects_mixed_native_feature_property_array() -> None:
    metadata = valid_metadata()
    metadata["features"] = {
        "source": "history",
        "summary": {},
        "items": [
            {
                "persistent_id": "Body",
                "name": "Body",
                "type": "BODY",
                "properties": {"InvalidMixedValues": ["one", 2]},
            }
        ],
    }

    with pytest.raises(MetadataSchemaError, match=r"features\.items\.0\.properties"):
        validate_part_metadata(metadata)


def test_accepts_normalized_drawing_worker_contract() -> None:
    metadata = valid_metadata()
    metadata["drawings"] = [
        {
            "stable_id": "main-drawing",
            "filename": "bracket.pdf",
            "worker_contract_version": "drawing-v1",
            "page_count": 1,
            "sections": [
                {
                    "stable_id": "notes",
                    "kind": "NOTES",
                    "page": 1,
                    "text": "REMOVE ALL BURRS",
                    "bbox": [10, 20, 100, 40],
                }
            ],
            "entities": [
                {
                    "stable_id": "tol-1",
                    "type": "TOLERANCE",
                    "page": 1,
                    "value": 0.1,
                    "unit": "mm",
                }
            ],
        }
    ]

    assert validate_part_metadata(metadata) is metadata


def test_accepts_freecad_drawing_semantics() -> None:
    metadata = valid_metadata()
    metadata["drawings"] = [{
        "stable_id": "drawing",
        "filename": "drawing-previews.json",
        "worker_contract_version": "freecad-techdraw-v1",
        "entities": [
            {"stable_id": "dimension", "type": "DIMENSION", "page": 1,
             "dimension_kind": "diameter", "tolerance": {
                 "enabled": True, "upper": 0.2, "lower": -0.1, "format": None,
                 "symmetric": False, "arbitrary": True,
                 "upper_format": "+%.2f", "lower_format": "-%.2f",
             }, "drawing_references": [
                 {"object": "View", "document": "Part", "subelements": ["Edge0"]},
             ]},
            {"stable_id": "section", "type": "VIEW", "page": 1,
             "view_kind": "section", "scale": 2.0,
             "projection_direction": "Vector (0, 0, 1)",
             "model_references": [{"object": "Body", "document": "Part",
                                   "subelements": ["Face1"]}]},
            {"stable_id": "balloon", "type": "BALLOON", "page": 1,
             "item_reference": "12"},
            {"stable_id": "center", "type": "CENTERLINE", "page": 1},
            {"stable_id": "mark", "type": "CENTER_MARK", "page": 1},
            {"stable_id": "cosmetic", "type": "COSMETIC", "page": 1},
            {"stable_id": "weld", "type": "GDT", "page": 1,
             "symbol_kind": "weld_symbol", "weld_details": {
                 "field_weld": True, "all_around": False, "tail_text": "ISO 123"}},
        ],
    }]
    validate_part_metadata(metadata)


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda value: value.pop("source"), "$"),
        (
            lambda value: value["geometry"]["bounding_box"].__setitem__("x_mm", "120"),
            "geometry.bounding_box.x_mm",
        ),
        (
            lambda value: value["quality"].__setitem__("geometry", "good"),
            "quality.geometry",
        ),
        (
            lambda value: value["source"].__setitem__("extracted_at", "yesterday"),
            "source.extracted_at",
        ),
    ],
)
def test_rejects_invalid_part_metadata(mutate, path: str) -> None:
    metadata = deepcopy(valid_metadata())
    mutate(metadata)
    with pytest.raises(MetadataSchemaError, match=path.replace(".", r"\.")):
        validate_part_metadata(metadata)
