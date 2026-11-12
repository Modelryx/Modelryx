"""Native FreeCAD TechDraw extraction provider."""

from __future__ import annotations

import hashlib
import logging
import math
import time
from typing import Any

from extraction_provider import ProviderResult
from techdraw_semantics import (
    KNOWN_SYMBOL_HASHES,
    capability_status,
    entity_semantics,
    techdraw_entity_kind,
)

logger = logging.getLogger(__name__)


def _valid_2d_references(references: Any) -> bool:
    """Check projected entities exist before trusting a dimension's raw value."""
    if not isinstance(references, (list, tuple)) or not references:
        return False
    for reference in references:
        if not isinstance(reference, (list, tuple)) or len(reference) < 2:
            return False
        source, names = reference[0], reference[1]
        names = [names] if isinstance(names, str) else names
        if not isinstance(names, (list, tuple)) or not names:
            return False
        for name in names:
            method = (
                "getEdgeBySelection"
                if str(name).startswith("Edge")
                else "getVertexBySelection"
                if str(name).startswith("Vertex")
                else None
            )
            getter = getattr(source, method, None) if method else None
            if not callable(getter):
                return False
            try:
                shape = getter(str(name))
                if shape is None or shape.isNull():
                    return False
            except (AttributeError, RuntimeError, TypeError, ValueError, IndexError):
                return False
    return True


def _valid_3d_references(references: Any) -> bool:
    """Require every referenced native subelement to resolve to a non-null shape."""
    if not isinstance(references, (list, tuple)) or not references:
        return False
    for reference in references:
        if not isinstance(reference, (list, tuple)) or len(reference) < 2:
            return False
        target, names = reference[0], reference[1]
        names = [names] if isinstance(names, str) else names
        getter = getattr(target, "getSubObject", None)
        if not callable(getter) or not isinstance(names, (list, tuple)) or not names:
            return False
        for name in names:
            if not isinstance(name, str) or not name:
                return False
            try:
                shape = getter(name)
                if shape is None or shape.isNull():
                    return False
            except (AttributeError, RuntimeError, TypeError, ValueError, IndexError):
                return False
    return True


def techdraw_entity(
    view: Any, page_number: int, custom_properties: Any, stable_id: Any, document_identity: Any
) -> dict[str, Any] | None:
    """Map supported TechDraw objects into the current drawing entity vocabulary."""
    type_id = str(getattr(view, "TypeId", ""))
    entity_type = techdraw_entity_kind(type_id)
    if entity_type is None:
        return None
    properties = custom_properties(view)
    recognized_symbol = None
    if type_id == "TechDraw::DrawViewSymbol":
        # Symbol is arbitrary SVG content; retain a fingerprint, not raw markup or paths.
        symbol = properties.get("Symbol")
        properties = {
            key: value for key, value in properties.items()
            if key in {"EditableTexts", "Caption", "Label"}
        }
        if isinstance(symbol, str) and symbol.lstrip().startswith("<"):
            properties["symbol_sha256"] = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
            recognized_symbol = KNOWN_SYMBOL_HASHES.get(properties["symbol_sha256"])
            if recognized_symbol:
                entity_type = "GDT"
    if type_id in {"TechDraw::DrawTileWeld", "TechDraw::DrawTileWeldPython"}:
        properties = {
            key: value
            for key, value in properties.items()
            if key in {"CenterText", "LeftText", "RightText", "TileColumn", "TileRow"}
        }
        symbol = str(getattr(view, "SymbolFile", "") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if symbol:
            properties["SymbolFile"] = symbol
    entity: dict[str, Any] = {
        "stable_id": str(getattr(view, "Name", "") or stable_id(type_id, str(page_number))),
        "type": entity_type,
        "page": page_number,
        "properties": properties,
    }
    for field, candidates in {
        "value": ("Measurement",),
        "tolerance": ("Tolerance", "ToleranceFormatSpec"),
        "text": ("Text", "Caption", "Label"),
        "view": ("Caption", "Label", "Name"),
    }.items():
        value = next(
            (properties[name] for name in candidates if properties.get(name) is not None), None
        )
        if value is None:
            continue
        if field in {"text", "view"}:
            entity[field] = "\n".join(value) if isinstance(value, list) else str(value)
        elif isinstance(value, (str, int, float, bool, list)):
            entity[field] = value
    unit = properties.get("Unit") or properties.get("UnitSchema")
    if unit is not None:
        entity["unit"] = str(unit)
    entity.update(entity_semantics(type_id, entity_type, properties))
    if recognized_symbol:
        entity["symbol_kind"] = recognized_symbol
    if entity_type == "DIMENSION" and isinstance(entity.get("tolerance"), dict):
        kind = entity.get("dimension_kind")
        expected = (
            ("Angle", "deg")
            if kind == "angular"
            else ("Length", "mm")
            if kind in {"linear", "radial", "diameter"}
            else None
        )
        if expected and properties.get("ArbitraryTolerances") is False:
            for output_name, native_name in (
                ("upper", "OverTolerance"),
                ("lower", "UnderTolerance"),
            ):
                quantity = getattr(view, native_name, None)
                try:
                    if str(quantity.Unit.Type) != expected[0]:
                        continue
                    value = float(quantity.Value)
                    if math.isfinite(value):
                        entity["tolerance"][output_name] = round(value, 9)
                        entity["unit"] = expected[1]
                except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
                    continue
    if entity_type == "VIEW":
        direction = getattr(view, "Direction", None)
        try:
            coordinates = [float(getattr(direction, axis)) for axis in ("x", "y", "z")]
        except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
            coordinates = []
        if coordinates and all(math.isfinite(value) for value in coordinates):
            entity["projection_direction"] = [round(value, 9) for value in coordinates]
    references_2d = getattr(view, "References2D", None) if entity_type == "DIMENSION" else None
    usable_2d = _valid_2d_references(references_2d) if references_2d else False
    references_3d = getattr(view, "References3D", None) if entity_type == "DIMENSION" else None
    usable_3d = _valid_3d_references(references_3d) if references_3d else False
    measure_type = properties.get("MeasureType")
    references_valid = (
        usable_3d if measure_type == "True"
        else usable_2d if measure_type == "Projected"
        else usable_2d or (not references_2d and usable_3d) if measure_type is None
        else False
    )
    if entity_type == "DIMENSION":
        entity.pop("value", None)
    if (
        entity_type == "DIMENSION"
        and entity.get("dimension_kind") in {"linear", "radial", "diameter", "angular"}
        and references_valid
        and not any(
            str(state).casefold() not in {"up-to-date", "touched"}
            for state in (getattr(view, "State", ()) or ())
        )
    ):
        getter = getattr(view, "getRawValue", None)
        if callable(getter):
            try:
                value = float(getter())
                if math.isfinite(value):
                    entity["value"] = round(value, 9)
                    entity["unit"] = "deg" if entity["dimension_kind"] == "angular" else "mm"
            except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError) as error:
                logger.warning("Unable to read a TechDraw dimension: %s", error)
    model_references = []
    drawing_references = []
    for attribute in (
        "Source",
        "Sources",
        "BaseView",
        "SourceView",
        "Leader",
        "Owner",
        "TileParent",
        "References2D",
        "References3D",
    ):
        raw_value = getattr(view, attribute, None)
        values = (
            [raw_value]
            if isinstance(raw_value, tuple) and raw_value and getattr(raw_value[0], "Name", None)
            else raw_value
            if isinstance(raw_value, (tuple, list))
            else [raw_value]
        )
        for value in values:
            target = value[0] if isinstance(value, (tuple, list)) and value else value
            name = getattr(target, "Name", None)
            if not name:
                continue
            reference = {
                "object": str(name),
                "document": document_identity(getattr(target, "Document", None)),
            }
            if isinstance(value, (tuple, list)) and len(value) > 1:
                raw_subelements = value[1]
                subelements = (
                    [raw_subelements]
                    if isinstance(raw_subelements, str)
                    else list(raw_subelements)
                    if isinstance(raw_subelements, (tuple, list))
                    else []
                )
                if subelements:
                    reference["subelements"] = [str(item) for item in subelements]
            references = (
                drawing_references
                if str(getattr(target, "TypeId", "")).startswith("TechDraw::")
                else model_references
            )
            if reference not in references:
                references.append(reference)
    if model_references:
        entity["model_references"] = model_references
    if drawing_references:
        entity["drawing_references"] = drawing_references
    return entity


def techdraw_metadata(
    document: Any,
    custom_properties: Any,
    stable_id: Any,
    document_identity: Any,
    quantity_value: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Return CAD-neutral drawing and native sheet metadata for TechDraw pages."""
    pages = [
        obj
        for obj in getattr(document, "Objects", [])
        if getattr(obj, "TypeId", "") == "TechDraw::DrawPage"
    ]
    sheets: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    title_block: dict[str, Any] = {}
    entity_kinds: list[str] = []
    unknown_type_ids: set[str] = set()
    incomplete_dimensions = 0
    incomplete_gdt = 0
    failed_entities = 0
    failed_pages = 0
    unclassified_symbols = 0
    tiles_by_parent: dict[str, list[Any]] = {}
    if pages:
        for obj in getattr(document, "Objects", ()):
            if str(getattr(obj, "TypeId", "")) not in {
                "TechDraw::DrawTileWeld",
                "TechDraw::DrawTileWeldPython",
            }:
                continue
            try:
                parent_name = str(getattr(getattr(obj, "TileParent", None), "Name", "") or "")
            except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
                failed_entities += 1
                continue
            if parent_name:
                tiles_by_parent.setdefault(parent_name, []).append(obj)
    for index, page in enumerate(pages, start=1):
        snapshot = (len(entities), len(entity_kinds), len(sections), len(sheets),
                    dict(title_block), set(unknown_type_ids), incomplete_dimensions,
                    incomplete_gdt, failed_entities, unclassified_symbols)
        try:
            template = getattr(page, "Template", None)
            views = list(getattr(page, "Views", []) or [])
            welds = [view for view in views if "WeldSymbol" in str(getattr(view, "TypeId", ""))]
            tiles = sorted(
                (
                    obj
                    for weld in welds
                    for obj in tiles_by_parent.get(str(getattr(weld, "Name", "")), ())
                    if obj not in views
                ),
                key=lambda obj: str(getattr(obj, "Name", "")).casefold(),
            )
            for view in views + tiles:
                try:
                    entity = techdraw_entity(
                        view, index, custom_properties, stable_id, document_identity
                    )
                except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
                    failed_entities += 1
                    continue
                if entity is not None:
                    entities.append(entity)
                    entity_kinds.append(str(entity["type"]))
                    if (str(getattr(view, "TypeId", "")) == "TechDraw::DrawViewSymbol"
                            and entity["type"] != "GDT"):
                        unclassified_symbols += 1
                    if entity["type"] == "DIMENSION" and (
                        not entity.get("dimension_kind")
                        or not isinstance(entity.get("value"), (int, float))
                        or isinstance(entity.get("value"), bool)
                    ):
                        incomplete_dimensions += 1
                    if entity["type"] == "GDT":
                        incomplete_gdt += 1
                else:
                    type_id = str(getattr(view, "TypeId", ""))
                    if type_id.startswith("TechDraw::"):
                        unknown_type_ids.add(type_id)
            editable_texts = getattr(template, "EditableTexts", None)
            if isinstance(editable_texts, dict):
                page_title_block = {}
                for name, value in editable_texts.items():
                    if isinstance(value, (str, int, float, bool)) and str(value).strip():
                        title_block[str(name)] = value
                        page_title_block[str(name)] = value
                if page_title_block:
                    sections.append(
                        {
                            "stable_id": f"{getattr(page, 'Name', index)}-title-block",
                            "kind": "TITLE_BLOCK",
                            "page": index,
                            "text": "\n".join(
                                f"{name}: {value}" for name, value in page_title_block.items()
                            ),
                        }
                    )
            sheets.append(
                {
                    "stable_id": str(getattr(page, "Name", "") or f"techdraw-page-{index}"),
                    "name": str(
                        getattr(page, "Label", "") or getattr(page, "Name", "") or f"Page {index}"
                    ),
                    "page": index,
                    "is_model_space": False,
                    "width_mm": round(
                        max(1.0, quantity_value(getattr(page, "PageWidth", None), 297.0)), 3
                    ),
                    "height_mm": round(
                        max(1.0, quantity_value(getattr(page, "PageHeight", None), 210.0)), 3
                    ),
                    "view_count": len(views),
                    "title_block_name": (
                        str(getattr(template, "Label", "") or getattr(template, "Name", ""))
                        if template is not None
                        else None
                    ),
                }
            )
        except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
            (entity_len, kinds_len, section_len, sheet_len, old_title, old_unknown,
             incomplete_dimensions, incomplete_gdt, failed_entities, unclassified_symbols) = snapshot
            del entities[entity_len:]
            del entity_kinds[kinds_len:]
            del sections[section_len:]
            del sheets[sheet_len:]
            title_block = old_title
            unknown_type_ids = old_unknown
            failed_pages += 1
    drawings = (
        [
            {
                "stable_id": "freecad-techdraw",
                "filename": "drawing-previews.json",
                "mime_type": "application/json",
                "page_count": len(pages),
                "worker_contract_version": "freecad-techdraw-v1",
                "sections": sections,
                "entities": entities,
            }
        ]
        if sheets
        else []
    )
    evidence = {
        "entity_kinds": sorted(entity_kinds),
        "unknown_type_ids": sorted(unknown_type_ids),
        "unknown_count": len(unknown_type_ids),
        "incomplete_dimension_count": incomplete_dimensions,
        "incomplete_gdt_count": incomplete_gdt,
        "failed_entity_count": failed_entities,
        "failed_page_count": failed_pages,
        "unclassified_symbol_count": unclassified_symbols,
        "drawing_status": (
            "partial"
            if unknown_type_ids or incomplete_dimensions or incomplete_gdt or failed_entities or failed_pages or unclassified_symbols
            else "complete"
            if sheets
            else "not_applicable"
        ),
        "dimension_status": capability_status(
            entity_kinds,
            target_kind="DIMENSION",
            unknown_count=len(unknown_type_ids) + incomplete_dimensions + failed_entities + failed_pages,
        ),
        "gdt_status": capability_status(
            entity_kinds,
            target_kind="GDT",
            unknown_count=len(unknown_type_ids) + incomplete_gdt + failed_entities + failed_pages + unclassified_symbols,
        ),
    }
    return drawings, sheets, title_block, evidence


class TechDrawExtractor:
    def __init__(
        self, custom_properties: Any, stable_id: Any, document_identity: Any, quantity_value: Any
    ):
        self.custom_properties = custom_properties
        self.stable_id = stable_id
        self.document_identity = document_identity
        self.quantity_value = quantity_value

    def extract(self, document: Any) -> ProviderResult:
        started = time.monotonic()
        diagnostics: list[dict[str, Any]] = []
        evidence: dict[str, Any] = {
            "drawing_status": "not_applicable",
            "dimension_status": "not_applicable",
            "gdt_status": "not_applicable",
            "unknown_count": 0,
            "unknown_type_ids": [],
            "incomplete_dimension_count": 0,
            "incomplete_gdt_count": 0,
            "failed_entity_count": 0,
        }
        failed = False
        try:
            drawings, sheets, title_block, evidence = techdraw_metadata(
                document,
                self.custom_properties,
                self.stable_id,
                self.document_identity,
                self.quantity_value,
            )
            unknown = int(evidence["unknown_count"])
            incomplete_dimensions = int(evidence["incomplete_dimension_count"])
            incomplete_gdt = int(evidence["incomplete_gdt_count"])
            failed_entities = int(evidence["failed_entity_count"])
            failed_pages = int(evidence["failed_page_count"])
            symbols = int(evidence["unclassified_symbol_count"])
            skipped = unknown + incomplete_dimensions + incomplete_gdt + failed_entities + failed_pages + symbols
            if symbols:
                diagnostics.append({
                    "section": "drawings", "code": "TECHDRAW_SYMBOL_SEMANTICS_UNAVAILABLE",
                    "message": f"{symbols} generic TechDraw symbols were retained without interpreting their semantics.",
                })
            if failed_pages:
                diagnostics.append({"section": "drawings", "code": "TECHDRAW_PAGE_EXTRACTION_FAILED",
                                    "message": f"{failed_pages} TechDraw pages could not be inspected."})
            if failed_entities:
                diagnostics.append(
                    {
                        "section": "drawings",
                        "code": "TECHDRAW_ENTITY_EXTRACTION_FAILED",
                        "message": f"{failed_entities} TechDraw entities could not be inspected.",
                    }
                )
            if unknown:
                diagnostics.append(
                    {
                        "section": "drawings",
                        "code": "UNSUPPORTED_TECHDRAW_OBJECT",
                        "message": "Unsupported TechDraw object types were preserved as partial evidence: "
                        + ", ".join(evidence["unknown_type_ids"]),
                    }
                )
            if incomplete_dimensions:
                diagnostics.append(
                    {
                        "section": "drawings",
                        "code": "TECHDRAW_DIMENSION_INCOMPLETE",
                        "message": f"{incomplete_dimensions} TechDraw dimensions lack a supported kind or measured value.",
                    }
                )
            if incomplete_gdt:
                diagnostics.append(
                    {
                        "section": "drawings",
                        "code": "TECHDRAW_GDT_INCOMPLETE",
                        "message": f"{incomplete_gdt} TechDraw symbols were classified without full GD&T semantics.",
                    }
                )
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            drawings, sheets, title_block, skipped, failed = [], [], {}, 1, True
            diagnostics.append(
                {
                    "section": "drawings",
                    "code": "TECHDRAW_EXTRACTION_FAILED",
                    "message": f"TechDraw metadata could not be extracted: {error}",
                }
            )
        return ProviderResult(
            records=drawings,
            diagnostics=diagnostics,
            evidence={
                "sheets": sheets,
                "title_block": title_block,
                "drawing": evidence,
                "skipped": skipped,
                "failed": failed,
            },
            capability="unavailable" if failed else str(evidence["drawing_status"]),
            duration_ms=(time.monotonic() - started) * 1000,
        )
