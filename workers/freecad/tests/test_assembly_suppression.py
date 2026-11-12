from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from assembly_suppression import native_suppression


def test_native_extension_is_authoritative() -> None:
    obj = SimpleNamespace(
        PropertiesList=["Suppressed"],
        Suppressed=True,
        hasExtension=lambda name: name == "App::SuppressibleExtension",
    )
    assert native_suppression(obj) == (True, "native")
    obj.Suppressed = False
    assert native_suppression(obj) == (False, "native")


def test_custom_suppression_property_is_not_assumed_authoritative() -> None:
    obj = SimpleNamespace(
        PropertiesList=["Suppressed"], Suppressed=True, hasExtension=lambda _: False
    )
    assert native_suppression(obj) == (None, "unverified")
    obj.Suppressed = "true"
    obj.hasExtension = lambda _: True
    assert native_suppression(obj) == (None, "unavailable")
