"""Repeated native link properties must not duplicate viewer occurrence nodes."""

from types import SimpleNamespace

from native_hierarchy import unique_hierarchy_children


def child(name, filename, document_name="Document"):
    return SimpleNamespace(Name=name, Document=SimpleNamespace(
        FileName=filename, Name=document_name))


def test_multiple_properties_referencing_one_child_preserve_order_and_unique_nodes():
    sketch = child("Sketch", "/package/window.FCStd")
    sill = child("Sill", "/package/window.FCStd")
    assert unique_hierarchy_children([sketch, sketch, sill, sketch, sill]) == [sketch, sill]


def test_distinct_documents_with_identical_basename_and_object_name_remain_distinct():
    first = child("Sketch", "/package/one/window.FCStd", "Window")
    second = child("Sketch", "/package/two/window.FCStd", "Window001")
    assert unique_hierarchy_children([first, second, first]) == [first, second]


def test_child_is_retained_for_each_parent_traversal():
    sketch = child("Sketch", "/package/window.FCStd")
    assert unique_hierarchy_children([sketch, sketch]) == [sketch]
    assert unique_hierarchy_children([sketch, sketch]) == [sketch]
