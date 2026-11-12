"""Read engineering material names without serializing FreeCAD internals."""

import logging
import math
import re
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)
_shape_material_warning_emitted = False


def material_name(obj: Any) -> str | None:
    # Preserve explicit custom-property overrides used by CAD material edits.
    names = list(getattr(obj, "PropertiesList", []))
    for name in names:
        if name.casefold() not in {"material", "materialname", "material_name"}:
            continue
        try:
            value = getattr(obj, name)
        except Exception as exc:  # noqa: BLE001 - FreeCAD property access can raise native exceptions
            logger.debug("Could not inspect FreeCAD material property %s: %s", name, exc)
            continue
        if isinstance(value, Mapping):
            value = value.get("Name")
        if isinstance(value, str) and value.strip():
            return value.strip()

    # Modern FreeCAD stores the assigned material as a Materials::Material object.
    # Its automatic Default material is not an engineering material assignment.
    try:
        material = getattr(obj, "ShapeMaterial", None)
        if getattr(material, "UUID", None) == "7f9fd73b-50c9-41d8-b7b2-575a030c1eeb":
            return None
        name = getattr(material, "Name", None)
        if isinstance(name, str) and name.strip() and name.strip().casefold() != "default":
            return name.strip()
    except Exception as exc:  # noqa: BLE001 - FreeCAD material APIs can raise native exceptions
        global _shape_material_warning_emitted
        if not _shape_material_warning_emitted:
            logger.warning(
                "Failed to inspect FreeCAD ShapeMaterial on at least one object: %s",
                exc,
            )
            _shape_material_warning_emitted = True
    return None


def _density_value(value: Any, *, numeric_is_kg_m3: bool = False) -> float | None:
    get_value_as = getattr(value, "getValueAs", None)
    if callable(get_value_as):
        try:
            converted = get_value_as("kg/m^3")
            number = float(getattr(converted, "Value", converted))
            return number if math.isfinite(number) and number > 0 else None
        except Exception as exc:  # noqa: BLE001 - FreeCAD quantity implementations vary
            logger.debug("Could not convert FreeCAD density quantity: %s", exc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return number if numeric_is_kg_m3 and math.isfinite(number) and number > 0 else None
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"\s*([0-9]+(?:\.[0-9]+)?)\s*(kg/m(?:\^?3|³)|g/cm(?:\^?3|³))\s*",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None
    number = float(match.group(1))
    converted = number * 1000.0 if match.group(2).casefold().startswith("g/") else number
    return converted if math.isfinite(converted) and converted > 0 else None


def material_density_kg_m3(obj: Any) -> float | None:
    """Return an explicitly unit-qualified material density in kg/m³."""
    for name in getattr(obj, "PropertiesList", []):
        key = "".join(character for character in name.casefold() if character.isalnum())
        if key not in {"density", "materialdensity", "densitykgm3"}:
            continue
        try:
            value = getattr(obj, name)
        except Exception as exc:  # noqa: BLE001 - FreeCAD properties may throw while resolving
            logger.debug("Could not read FreeCAD density property %s: %s", name, exc)
            continue
        density = _density_value(value, numeric_is_kg_m3=key == "densitykgm3")
        if density is not None:
            return density
    try:
        material = getattr(obj, "ShapeMaterial", None)
        card = getattr(material, "Card", None)
        if isinstance(card, Mapping):
            for name, value in card.items():
                if "density" in str(name).casefold():
                    density = _density_value(value)
                    if density is not None:
                        return density
    except Exception as exc:  # noqa: BLE001 - FreeCAD material APIs can raise native exceptions
        logger.warning("Failed to inspect FreeCAD material density: %s", exc)
    return None
