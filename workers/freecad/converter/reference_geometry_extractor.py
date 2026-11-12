"""Extract FreeCAD datum geometry with isolated object-level failures."""

from __future__ import annotations

import time
from typing import Any

from extraction_provider import ProviderResult


class ReferenceGeometryExtractor:
    def __init__(self, kind: Any, reference: Any, placement: Any, numeric: Any, visible: Any):
        self.kind = kind
        self.reference = reference
        self.placement = placement
        self.numeric = numeric
        self.visible = visible

    def extract(self, document: Any) -> ProviderResult:
        started = time.monotonic()
        records: list[dict[str, Any]] = []
        skipped = 0
        diagnostics: list[dict[str, Any]] = []
        try:
            objects = sorted(getattr(document, "Objects", ()), key=lambda item: str(item.Name).casefold())
            for obj in objects:
                try:
                    type_id = str(getattr(obj, "TypeId", ""))
                    kind = self.kind(type_id)
                    if kind is None or kind == "origin":
                        continue
                    if type_id.startswith("App::") and any(
                        "Origin" in str(getattr(parent, "TypeId", ""))
                        for parent in (getattr(obj, "InList", ()) or ())
                    ):
                        continue
                    properties = set(getattr(obj, "PropertiesList", ()))
                    raw_support = getattr(obj, "AttachmentSupport", None)
                    if raw_support is None:
                        raw_support = getattr(obj, "Support", None)
                    values = raw_support if isinstance(raw_support, list) else [raw_support]
                    supports = [ref for value in values if (ref := self.reference(value)) is not None]
                    record: dict[str, Any] = {
                        "name": str(obj.Name), "label": str(getattr(obj, "Label", obj.Name)),
                        "kind": kind, "source_type": type_id, "visible": self.visible(obj),
                        "map_mode": str(getattr(obj, "MapMode", "") or "Deactivated"),
                        "map_reversed": bool(getattr(obj, "MapReversed", False)), "support": supports,
                    }
                    for field, property_name in (("placement", "Placement"),
                                                  ("attachment_offset", "AttachmentOffset")):
                        value = self.placement(getattr(obj, property_name, None))
                        if value is not None:
                            record[field] = value
                    dimensions = {}
                    for field, property_name in (("length_mm", "Length"), ("width_mm", "Width")):
                        if property_name in properties:
                            value, _unit = self.numeric(getattr(obj, property_name, None))
                            if value is not None:
                                dimensions[field] = round(value, 9)
                    if dimensions:
                        record["dimensions"] = dimensions
                    records.append(record)
                except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
                    skipped += 1
            if skipped:
                diagnostics.append({"section": "reference_geometry", "code": "REFERENCE_GEOMETRY_EXTRACTION_FAILED",
                                    "message": f"{skipped} FreeCAD datum records could not be extracted."})
        except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError) as error:
            skipped += 1
            diagnostics.append({"section": "reference_geometry", "code": "REFERENCE_GEOMETRY_EXTRACTION_FAILED",
                                "message": f"Reference geometry semantics could not be extracted: {error}"})
        return ProviderResult(records=records, diagnostics=diagnostics, evidence={"skipped": skipped},
                              capability="partial" if skipped else "complete" if records else "not_applicable",
                              duration_ms=(time.monotonic() - started) * 1000)
