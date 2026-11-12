"""FreeCAD-independent TechDraw entity classification and capability evidence."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_GDT_TOKENS = ("GeometricTolerance", "DatumFeature", "SurfaceFinish", "WeldSymbol", "TileWeld")
_SUPPORTED_VIEWS = {
    "TechDraw::DrawViewPart",
    "TechDraw::DrawViewSection",
    "TechDraw::DrawViewDetail",
}

# Exact bundled FreeCAD 1.1.3 SVG contents. A match establishes only the
# symbol family; it does not establish its datum label, value, or reference.
KNOWN_SYMBOL_HASHES = {
    "549fc17aa3518e5d17010d352cd82b9cfd37ad1aed825a5601d8b717699374fc": "datum_feature",
    "636348ca20615c47b0b101009edd1278bce59a2c3f759b12b447651214b65ea0": "datum_feature",
    "47538c4186c4e147f7493d61d4714e700f9aee65253227e76563881181272978": "datum_feature",
    "e577366563ddc165ca918aaa9807e3fcc25c50e8dbe833a6b24dd7f586591587": "surface_finish",
    "c408d3ba81237492a2f2aa8a69ca4748e3100ff3f622e358fe2f70a9bb19c266": "surface_finish",
    "a18a973e9df53de8686b0d97123a42025d8dc3c98259f891230a8537d0010877": "surface_finish",
    "caa640dad83aec8d7040b35af9a42e6907daba12234e5226e64594219b247578": "surface_finish",
    "9d2a85bb9c841064d414206ebd908f01fa07ca728080676f4c32434e4c383a6d": "surface_finish",
    "5dda058020126c90acf75ba625bec5e7dac5efd8674f1b7fa95b124074792a45": "geometric_tolerance",
    "1cf46d4a5e5eced60ad358b9c6c2d34f57c06326ee6505be76252bca8b769d90": "geometric_tolerance",
    "d63302094a828ae1a6d78782c59b2583dda72f53b18ed7141ba3c62f454fa58d": "geometric_tolerance",
    "944ef847bc2598d045365adf01d08ab68219ab07d8ca2d54d1225954cc672d29": "geometric_tolerance",
    "aeaf8237e35d1d32e31ff943e053892279e8357dbd6f48c6722902aa80468359": "geometric_tolerance",
    "937b444768e637b2f0bfe4a0f56d81325335c35721042a8d7e3e97c375ccd4ec": "geometric_tolerance",
    "90e3b9b1fbd39cea56f0962e73d6e812987b10ae4ecdb741649290dd729cfc11": "geometric_tolerance",
    "e2e00955dfffa5e15281bb3c3d7bf2ac6921999f5b14395ba66e5aee729ad242": "geometric_tolerance",
    "1dc45c40685d5a9383c915c23ab3d297ceac733edb04eb3ac96db6dd4aa71471": "geometric_tolerance",
    "62bc6ef7b274030a82c80e68f1315eb3b7586831d98fb8a6cf4dea2bc49f8d85": "geometric_tolerance",
    "a965f616d1fb8289becc1821429b6397ab5c854430fc95b68a17ed734006c1bc": "geometric_tolerance",
    "14e02038abddd7df88e7b410a5e3266d0c2c24d46a7e987027783dcfda6e74b1": "geometric_tolerance",
    "e507ac47db7485f0303b8da818420467f041d1a5089195bee48d4c5c0bb5fab3": "geometric_tolerance",
    "b4b5e9c1efc055a7539ede7997226599fa2fb785bbdf815332fd85fe4e191236": "geometric_tolerance",
    "70ed5438fcb2e40095b0910e329270ca3e91507fe521a6f06de09adc14397e0a": "geometric_tolerance",
    "1d48f199460d643bf133c41016fd558b16ae430013bbdf1228dcc0f1136c7b97": "geometric_tolerance",
    "94290cdb8e46e43f62a0e706081f179b0f2242e8409814424a77b9df35acd517": "geometric_tolerance",
    "2bc85d161c17c3c5fc500341f72ab035f4df8e2292c077ef712ec92c602cb65f": "geometric_tolerance",
    "1a75c9441643185e269a4ad392554c12ada224d8d2cd9cd4cdde6863cf9aeb6e": "geometric_tolerance",
}


def techdraw_entity_kind(type_id: str) -> str | None:
    """Return a conservative semantic kind for a TechDraw document object."""
    if not type_id.startswith("TechDraw::"):
        return None
    if "Dimension" in type_id:
        return "DIMENSION"
    if any(token in type_id for token in _GDT_TOKENS):
        return "GDT"
    if "Balloon" in type_id:
        return "BALLOON"
    if "Annotation" in type_id:
        return "ANNOTATION"
    if type_id == "TechDraw::DrawViewSymbol":
        return "ANNOTATION"
    if "CenterLine" in type_id:
        return "CENTERLINE"
    if "CenterMark" in type_id:
        return "CENTER_MARK"
    if "Cosmetic" in type_id:
        return "COSMETIC"
    if type_id in _SUPPORTED_VIEWS:
        return "VIEW"
    return None


def dimension_kind(properties: dict[str, Any]) -> str | None:
    """Normalize FreeCAD's dimension type without guessing from display text."""
    raw = str(properties.get("Type") or properties.get("DimensionType") or "").casefold()
    # These are FreeCAD 1.1's native Type enumeration values. Unknown values
    # remain partial instead of being classified by a coincidental substring.
    return {
        "distance": "linear", "distancex": "linear", "distancey": "linear",
        "distancez": "linear", "radius": "radial", "diameter": "diameter",
        "angle": "angular", "angle3pt": "angular",
    }.get(raw)


def capability_status(
    entity_kinds: Iterable[str], *, target_kind: str, unknown_count: int = 0
) -> str:
    """Report capability from parsed evidence, not merely from page existence."""
    relevant_count = sum(kind == target_kind for kind in entity_kinds)
    if unknown_count:
        return "partial"
    if relevant_count == 0:
        return "not_applicable"
    return "complete"


def entity_semantics(type_id: str, entity_kind: str, properties: dict[str, Any]) -> dict[str, Any]:
    """Project explicit native TechDraw properties into searchable semantics."""
    result: dict[str, Any] = {}
    if entity_kind == "DIMENSION":
        if (kind := dimension_kind(properties)) is not None:
            result["dimension_kind"] = kind
        tolerance = {}
        for field, name in (
            ("enabled", "Tolerance"),
            ("upper", "OverTolerance"),
            ("lower", "UnderTolerance"),
            ("format", "ToleranceFormatSpec"),
            ("symmetric", "EqualTolerance"),
            ("arbitrary", "ArbitraryTolerances"),
            ("upper_format", "FormatSpecOverTolerance"),
            ("lower_format", "FormatSpecUnderTolerance"),
        ):
            if name in properties and properties[name] is not None:
                tolerance[field] = properties[name]
        if tolerance:
            result["tolerance"] = tolerance
    if entity_kind == "VIEW":
        if "Section" in type_id:
            result["view_kind"] = "section"
        elif "Detail" in type_id:
            result["view_kind"] = "detail"
        else:
            result["view_kind"] = "projection"
        for output_name, native_name in (
            ("scale", "Scale"),
            ("scale_type", "ScaleType"),
            ("projection_direction", "Direction"),
            ("rotation_deg", "Rotation"),
        ):
            value = properties.get(native_name)
            if value not in (None, ""):
                result[output_name] = value
    if entity_kind == "BALLOON":
        value = properties.get("BalloonText") or properties.get("Text")
        if value not in (None, ""):
            result["item_reference"] = value
    if entity_kind == "GDT":
        result["symbol_kind"] = next(
            (
                value
                for token, value in (
                    ("GeometricTolerance", "geometric_tolerance"),
                    ("DatumFeature", "datum_feature"),
                    ("SurfaceFinish", "surface_finish"),
                    ("WeldSymbol", "weld_symbol"),
                    ("TileWeld", "weld_symbol_tile"),
                )
                if token in type_id
            ),
            "gdt",
        )
        details = {}
        if result["symbol_kind"] == "weld_symbol":
            for field, name in (("all_around", "AllAround"), ("field_weld", "FieldWeld"),
                                ("alternating", "AlternatingWeld")):
                if isinstance(properties.get(name), bool):
                    details[field] = properties[name]
            if isinstance(properties.get("TailText"), str):
                details["tail_text"] = properties["TailText"]
        elif result["symbol_kind"] == "weld_symbol_tile":
            for field, name in (("left_text", "LeftText"), ("center_text", "CenterText"),
                                ("right_text", "RightText"), ("symbol_file", "SymbolFile")):
                if isinstance(properties.get(name), str):
                    details[field] = properties[name]
            for field, name in (("column", "TileColumn"), ("row", "TileRow")):
                if isinstance(properties.get(name), int) and not isinstance(properties[name], bool):
                    details[field] = properties[name]
        if details:
            result["weld_details"] = details
    return result
