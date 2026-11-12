from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from cad_references import object_reference, object_references


def test_references_use_package_qualified_document_identity() -> None:
    first = SimpleNamespace(Name="Body", Document=SimpleNamespace(package_path="one/part.FCStd"))
    second = SimpleNamespace(Name="Body", Document=SimpleNamespace(package_path="two/part.FCStd"))
    identity = lambda document: document.package_path

    references = object_references([(first, ["Face1"]), (second, "Edge2")], identity)
    assert references == [
        {"object": "Body", "document": "one/part.FCStd", "subelements": ["Face1"]},
        {"object": "Body", "document": "two/part.FCStd", "subelements": ["Edge2"]},
    ]
    assert object_reference((first, "Face1"), identity) == references[0]
