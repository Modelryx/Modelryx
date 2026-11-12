"""Read explicit saved view-provider visibility from an FCStd archive."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree


def read_saved_visibility(path: Path) -> dict[str, bool]:
    """Return only unambiguous Boolean visibility values; absence means unknown."""
    try:
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("GuiDocument.xml"))
    except (OSError, KeyError, ValueError, ElementTree.ParseError, zipfile.BadZipFile):
        return {}
    values: dict[str, bool] = {}
    ambiguous: set[str] = set()
    for provider in root.iter("ViewProvider"):
        name = provider.get("name")
        if not name:
            continue
        properties = provider.findall("./Properties/Property[@name='Visibility']")
        boolean = properties[0].find("./Bool") if len(properties) == 1 else None
        value = boolean.get("value", "").casefold() if boolean is not None else ""
        if name in values or name in ambiguous or value not in {"true", "false"}:
            values.pop(name, None)
            ambiguous.add(name)
            continue
        values[name] = value == "true"
    return values
