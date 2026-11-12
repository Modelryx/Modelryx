from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from link_array_geometry import filtered_expanded_array_shape, filtered_face_appearances


def _element(suppressed):
    return SimpleNamespace(
        PropertiesList=["Suppressed"],
        Suppressed=suppressed,
        hasExtension=lambda name: name == "App::SuppressibleExtension",
    )


def test_filter_rejects_incomplete_activity_and_excessive_element_count() -> None:
    array = SimpleNamespace(
        ElementCount=2, ShowElement=True, ElementList=[_element(True), _element(None)],
        VisibilityList=[True, True],
    )
    assert filtered_expanded_array_shape(array, None, 2) == ("unavailable", None)
    array.ElementList[1] = _element(False)
    assert filtered_expanded_array_shape(array, None, 1) == ("unavailable", None)
    array.ShowElement = False
    assert filtered_expanded_array_shape(array, None, 2) == ("unavailable", None)


def test_filter_omits_geometry_when_every_element_is_suppressed() -> None:
    array = SimpleNamespace(
        ElementCount=1, ShowElement=True, ElementList=[_element(True)], VisibilityList=[True],
        Placement=SimpleNamespace(inverse=lambda: object()),
    )
    assert filtered_expanded_array_shape(array, None, 1) == ("empty", None)


def test_collapsed_hidden_array_requires_complete_visibility_and_empty_native_shape() -> None:
    array = SimpleNamespace(ElementCount=2, ShowElement=False, VisibilityList=[False, False])
    assert filtered_expanded_array_shape(array, None, 2) == ("empty", None)
    array.VisibilityList = [False]
    assert filtered_expanded_array_shape(array, None, 2) == ("unavailable", None)
    array.VisibilityList = [False, False]
    array.Shape = SimpleNamespace(isNull=lambda: False)
    assert filtered_expanded_array_shape(array, None, 2) == ("unavailable", None)


def test_filtered_appearance_mapping_rejects_ambiguous_faces() -> None:
    class Face:
        Area = 1.0
        BoundBox = SimpleNamespace(XMin=0, YMin=0, ZMin=0, XMax=1, YMax=1, ZMax=0)

        @staticmethod
        def isValid():
            return True

        @staticmethod
        def cut(_other):
            return SimpleNamespace(Area=0, isValid=lambda: True)

    original = SimpleNamespace(Faces=[Face(), Face()])
    filtered = SimpleNamespace(Faces=[Face()])
    assert filtered_face_appearances(original, filtered, ["red", "blue"]) is None
    assert filtered_face_appearances(SimpleNamespace(Faces=[Face()]), filtered, ["red"]) == ["red"]
    invalid = Face()
    invalid.isValid = lambda: False
    assert filtered_face_appearances(SimpleNamespace(Faces=[invalid]), filtered, ["red"]) is None
