"""Distinguish native package dependencies from managed assembly occurrences."""


def is_managed_bom_reference(reference: dict) -> bool:
    properties = reference.get("properties")
    return not isinstance(properties, dict) or properties.get("is_bom_occurrence") is not False
