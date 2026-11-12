import re
from dataclasses import dataclass

_LENGTH_FACTORS = {
    "mm": 1.0,
    "millimeter": 1.0,
    "millimeters": 1.0,
    "cm": 10.0,
    "centimeter": 10.0,
    "centimeters": 10.0,
    "m": 1000.0,
    "meter": 1000.0,
    "meters": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    "ft": 304.8,
    "foot": 304.8,
    "feet": 304.8,
}
_MASS_FACTORS = {
    "kg": 1.0,
    "kilogram": 1.0,
    "kilograms": 1.0,
    "g": 0.001,
    "gram": 0.001,
    "grams": 0.001,
    "lb": 0.45359237,
    "lbs": 0.45359237,
    "pound": 0.45359237,
    "pounds": 0.45359237,
}


def length_to_mm(value: float, unit: str) -> float:
    try:
        return value * _LENGTH_FACTORS[unit.casefold()]
    except KeyError as exc:
        raise ValueError(f"unsupported length unit: {unit}") from exc


def mass_to_kg(value: float, unit: str) -> float:
    try:
        return value * _MASS_FACTORS[unit.casefold()]
    except KeyError as exc:
        raise ValueError(f"unsupported mass unit: {unit}") from exc


@dataclass(frozen=True)
class NormalizedMaterial:
    family: str
    grade: str | None = None
    temper: str | None = None
    raw: str | None = None


def normalize_material(value: str) -> NormalizedMaterial:
    raw = value.strip()
    compact = re.sub(r"[_\s-]+", " ", raw.casefold()).strip()
    family = compact
    if re.search(r"\b(aluminum|aluminium|al)\b", compact):
        family = "aluminum"
    elif "stainless" in compact:
        family = "stainless_steel"
    elif re.search(r"\b(steel|carbon steel)\b", compact):
        family = "steel"
    elif "titanium" in compact or re.search(r"\bti\b", compact):
        family = "titanium"
    elif "brass" in compact:
        family = "brass"
    elif "copper" in compact:
        family = "copper"
    grade_match = re.search(r"\b(\d{3,4})\b", compact)
    temper_match = re.search(r"\b([tohw]\d{1,3})\b", compact, re.IGNORECASE)
    return NormalizedMaterial(
        family=family,
        grade=grade_match.group(1) if grade_match else None,
        temper=temper_match.group(1).upper() if temper_match else None,
        raw=raw,
    )


def material_search_tokens(material: NormalizedMaterial) -> list[str]:
    aliases = {
        "aluminum": ["aluminum", "aluminium", "al_", "al "],
        "stainless_steel": ["stainless", "stainless_steel"],
    }
    tokens = list(aliases.get(material.family, [material.family]))
    if material.grade:
        tokens.append(material.grade)
    if material.temper:
        tokens.append(material.temper.casefold())
    return tokens
