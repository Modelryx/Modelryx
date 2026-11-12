"""Read explicit FreeCAD suppression without equating it with visibility."""

from __future__ import annotations

from typing import Any


def native_suppression(obj: Any) -> tuple[bool | None, str | None]:
    """Return a suppression state only when the native extension supports it."""
    try:
        properties = set(getattr(obj, "PropertiesList", ()) or ())
        checker = getattr(obj, "hasExtension", None)
        if not callable(checker):
            return None, "unverified" if "Suppressed" in properties else None
        if not checker("App::SuppressibleExtension"):
            return None, "unverified" if "Suppressed" in properties else None
        value = getattr(obj, "Suppressed", None)
        if not isinstance(value, bool):
            return None, "unavailable"
        return value, "native"
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None, "unavailable"
