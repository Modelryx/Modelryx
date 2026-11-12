from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from techdraw_extractor import TechDrawExtractor, techdraw_entity


def test_broken_page_preserves_other_pages_and_page_numbers() -> None:
    class BrokenPage:
        Name = "Broken"
        TypeId = "TechDraw::DrawPage"

        @property
        def Template(self):
            raise RuntimeError("unreadable template")

    view = SimpleNamespace(TypeId="TechDraw::DrawViewPart", Name="View")
    page = SimpleNamespace(TypeId="TechDraw::DrawPage", Name="Good", Views=[view])
    result = TechDrawExtractor(
        lambda _: {}, lambda *_: "id", lambda _: "doc", lambda _, default: default
    ).extract(SimpleNamespace(Objects=[BrokenPage(), page]))
    assert result.capability == "partial"
    assert result.records[0]["page_count"] == 2
    assert result.records[0]["entities"][0]["page"] == 2
    assert result.evidence["sheets"][0]["page"] == 2
    assert result.evidence["drawing"]["dimension_status"] == "partial"
    assert any(d["code"] == "TECHDRAW_PAGE_EXTRACTION_FAILED" for d in result.diagnostics)


def test_known_symbol_requires_exact_svg_fingerprint(monkeypatch) -> None:
    import hashlib
    import techdraw_extractor

    svg = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
    fingerprint = hashlib.sha256(svg.encode()).hexdigest()
    monkeypatch.setattr(techdraw_extractor, "KNOWN_SYMBOL_HASHES", {fingerprint: "datum_feature"})
    view = SimpleNamespace(TypeId="TechDraw::DrawViewSymbol", Name="Datum", Document=None)
    def extract(value):
        return techdraw_entity(view, 1, lambda _: {"Symbol": value}, lambda *_: "id", lambda _: "doc")

    recognized = extract(svg)
    assert recognized["type"] == "GDT"
    assert recognized["symbol_kind"] == "datum_feature"
    assert "Symbol" not in recognized["properties"]
    assert extract(svg + " ")["type"] == "ANNOTATION"


def test_unknown_drawing_entity_is_partial_even_without_known_dimensions() -> None:
    unknown = SimpleNamespace(TypeId="TechDraw::DrawViewFuture", Name="Unknown")
    page = SimpleNamespace(TypeId="TechDraw::DrawPage", Name="Page", Views=[unknown])
    result = TechDrawExtractor(
        lambda _obj: {}, lambda *_args: "id", lambda _doc: "doc", lambda _value, default: default
    ).extract(SimpleNamespace(Objects=[page]))
    assert result.capability == "partial"
    assert result.evidence["drawing"]["dimension_status"] == "partial"
    assert result.evidence["drawing"]["gdt_status"] == "partial"
    assert result.diagnostics[0]["code"] == "UNSUPPORTED_TECHDRAW_OBJECT"


def test_generic_symbol_retains_text_and_fingerprint_without_interpreting_svg() -> None:
    import hashlib

    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>Ra 3.2</text></svg>'
    view = SimpleNamespace(TypeId="TechDraw::DrawViewSymbol", Name="Symbol")
    page = SimpleNamespace(TypeId="TechDraw::DrawPage", Name="Page", Views=[view])
    result = TechDrawExtractor(
        lambda _: {"Symbol": svg, "EditableTexts": ["Ra 3.2"], "Other": "/private/path"},
        lambda *_: "id", lambda _: "doc", lambda _, default: default,
    ).extract(SimpleNamespace(Objects=[page]))
    entity = result.records[0]["entities"][0]
    assert entity["type"] == "ANNOTATION"
    assert entity["properties"] == {
        "EditableTexts": ["Ra 3.2"], "symbol_sha256": hashlib.sha256(svg.encode()).hexdigest()
    }
    assert result.evidence["drawing"]["gdt_status"] == "partial"
    assert result.evidence["drawing"]["dimension_status"] == "not_applicable"
    assert result.diagnostics[0]["code"] == "TECHDRAW_SYMBOL_SEMANTICS_UNAVAILABLE"


def test_unreadable_entity_does_not_discard_other_drawing_entities() -> None:
    broken = SimpleNamespace(TypeId="TechDraw::DrawViewPart", Name="Broken")
    valid = SimpleNamespace(TypeId="TechDraw::DrawViewPart", Name="Valid")
    page = SimpleNamespace(TypeId="TechDraw::DrawPage", Name="Page", Views=[broken, valid])

    def properties(obj):
        if obj is broken:
            raise RuntimeError("broken native property")
        return {}

    result = TechDrawExtractor(
        properties, lambda *_args: "id", lambda _doc: "doc", lambda _value, default: default
    ).extract(SimpleNamespace(Objects=[page]))
    assert result.capability == "partial"
    assert [entity["stable_id"] for entity in result.records[0]["entities"]] == ["Valid"]
    assert result.evidence["drawing"]["dimension_status"] == "partial"
    assert result.evidence["drawing"]["gdt_status"] == "partial"
    assert result.diagnostics[0]["code"] == "TECHDRAW_ENTITY_EXTRACTION_FAILED"


def test_derived_views_and_balloons_retain_their_source_view() -> None:
    source = SimpleNamespace(TypeId="TechDraw::DrawViewPart", Name="Base", Document=None)
    for type_id, attribute in (
        ("TechDraw::DrawViewSection", "BaseView"),
        ("TechDraw::DrawViewDetail", "BaseView"),
        ("TechDraw::DrawViewBalloon", "SourceView"),
    ):
        obj = SimpleNamespace(TypeId=type_id, Name="Derived", **{attribute: source})
        entity = techdraw_entity(obj, 1, lambda _obj: {}, lambda *_args: "id", lambda _doc: "Doc")
        assert entity["drawing_references"] == [{"object": "Base", "document": "Doc"}]


def test_projection_direction_is_retained_as_numeric_components() -> None:
    obj = SimpleNamespace(
        TypeId="TechDraw::DrawViewPart", Name="View", Direction=SimpleNamespace(x=0, y=0, z=1)
    )
    entity = techdraw_entity(
        obj,
        1,
        lambda _obj: {"Direction": "Vector (0, 0, 1)"},
        lambda *_args: "id",
        lambda _doc: "Doc",
    )
    assert entity["projection_direction"] == [0.0, 0.0, 1.0]


def test_native_tolerances_become_numbers_only_with_matching_quantity_units() -> None:
    length = SimpleNamespace(Unit=SimpleNamespace(Type="Length"), Value=0.25)
    wrong_unit = SimpleNamespace(Unit=SimpleNamespace(Type="Angle"), Value=-0.1)
    view = SimpleNamespace(
        TypeId="TechDraw::DrawViewDimension",
        Name="Dimension",
        OverTolerance=length,
        UnderTolerance=wrong_unit,
    )
    properties = {
        "Type": "DistanceX",
        "OverTolerance": "0.25 mm",
        "UnderTolerance": "-0.1 deg",
        "ArbitraryTolerances": False,
    }
    entity = techdraw_entity(
        view, 1, lambda _obj: properties, lambda *_args: "id", lambda _doc: "Doc"
    )
    assert entity["tolerance"]["upper"] == 0.25
    assert entity["tolerance"]["lower"] == "-0.1 deg"
    assert entity["unit"] == "mm"
    properties["ArbitraryTolerances"] = True
    entity = techdraw_entity(
        view, 1, lambda _obj: properties, lambda *_args: "id", lambda _doc: "Doc"
    )
    assert entity["tolerance"]["upper"] == "0.25 mm"


def test_unresolved_projected_dimension_does_not_report_a_zero_measurement() -> None:
    source = SimpleNamespace(
        TypeId="TechDraw::DrawViewPart",
        Name="View",
        Document=None,
        getEdgeBySelection=lambda _name: SimpleNamespace(isNull=lambda: True),
    )
    dimension = SimpleNamespace(
        TypeId="TechDraw::DrawViewDimension",
        Name="Angle",
        State=["Up-to-date"],
        References2D=[(source, ["Edge0"])],
        getRawValue=lambda: 0.0,
    )
    page = SimpleNamespace(TypeId="TechDraw::DrawPage", Name="Page", Views=[dimension])
    result = TechDrawExtractor(
        lambda _obj: {"Type": "Angle", "Measurement": 0.0},
        lambda *_args: "id",
        lambda _doc: "Doc",
        lambda _value, default: default,
    ).extract(SimpleNamespace(Objects=[page]))
    assert "value" not in result.records[0]["entities"][0]
    assert result.evidence["drawing"]["dimension_status"] == "partial"
    assert any(item["code"] == "TECHDRAW_DIMENSION_INCOMPLETE" for item in result.diagnostics)


def test_dimension_requires_resolved_native_3d_subelements() -> None:
    target = SimpleNamespace(Name="Part", TypeId="Part::Feature", Document=None,
                             getSubObject=lambda name: SimpleNamespace(isNull=lambda: name == "Face999"))
    view = SimpleNamespace(TypeId="TechDraw::DrawViewDimension", Name="Dimension", State=[],
                           References3D=[(target, ["Face999"])], getRawValue=lambda: 0.0)
    properties = lambda _: {"Type": "DistanceX", "Measurement": 0.0}
    entity = techdraw_entity(view, 1, properties, lambda *_: "id", lambda _: "doc")
    assert "value" not in entity
    view.References3D = [(target, ["Face1"])]
    view.getRawValue = lambda: 12.0
    entity = techdraw_entity(view, 1, properties, lambda *_: "id", lambda _: "doc")
    assert entity["value"] == 12.0 and entity["unit"] == "mm"
    view.References3D = []
    entity = techdraw_entity(view, 1, properties, lambda *_: "id", lambda _: "doc")
    assert "value" not in entity


def test_true_measurement_does_not_trust_valid_projected_reference_alone() -> None:
    projected = SimpleNamespace(Name="View", TypeId="TechDraw::DrawViewPart", Document=None,
                                getEdgeBySelection=lambda _: SimpleNamespace(isNull=lambda: False))
    view = SimpleNamespace(TypeId="TechDraw::DrawViewDimension", Name="Dimension", State=[],
                           References2D=[(projected, ["Edge0"])], References3D=[],
                           getRawValue=lambda: 0.0)
    entity = techdraw_entity(view, 1, lambda _: {"Type": "Distance", "MeasureType": "True"},
                             lambda *_: "id", lambda _: "doc")
    assert "value" not in entity


def test_weld_tiles_follow_their_parent_and_exclude_local_paths() -> None:
    weld = SimpleNamespace(TypeId="TechDraw::DrawWeldSymbol", Name="Weld", Document=None)
    tile = SimpleNamespace(
        TypeId="TechDraw::DrawTileWeld",
        Name="Tile",
        TileParent=weld,
        SymbolFile=r"C:\private\weld.svg",
        Document=None,
    )
    page = SimpleNamespace(TypeId="TechDraw::DrawPage", Name="Page", Views=[weld])
    result = TechDrawExtractor(
        lambda obj: (
            {"LeftText": "8", "SymbolIncluded": "/private/cache.svg"} if obj is tile else {}
        ),
        lambda *_args: "id",
        lambda _doc: "Doc",
        lambda _value, default: default,
    ).extract(SimpleNamespace(Objects=[page, weld, tile]))
    entities = {entity["stable_id"]: entity for entity in result.records[0]["entities"]}
    assert entities["Tile"]["properties"] == {"LeftText": "8", "SymbolFile": "weld.svg"}
    assert entities["Tile"]["weld_details"] == {"left_text": "8", "symbol_file": "weld.svg"}
    assert entities["Tile"]["drawing_references"] == [{"object": "Weld", "document": "Doc"}]
    assert result.evidence["drawing"]["gdt_status"] == "partial"


def test_weld_details_include_only_explicit_well_typed_properties() -> None:
    weld = SimpleNamespace(TypeId="TechDraw::DrawWeldSymbol", Name="Weld")
    entity = techdraw_entity(
        weld, 1,
        lambda _: {"FieldWeld": True, "AllAround": False, "AlternatingWeld": "unknown",
                   "TailText": "ISO 123"},
        lambda *_: "id", lambda _: "doc",
    )
    assert entity["weld_details"] == {
        "field_weld": True, "all_around": False, "tail_text": "ISO 123"
    }


def test_unreadable_weld_tile_does_not_discard_page() -> None:
    class BrokenTile:
        Name = "Broken"
        TypeId = "TechDraw::DrawTileWeld"

        @property
        def TileParent(self):
            raise RuntimeError("broken native link")

    weld = SimpleNamespace(TypeId="TechDraw::DrawWeldSymbol", Name="Weld")
    page = SimpleNamespace(TypeId="TechDraw::DrawPage", Name="Page", Views=[weld])
    result = TechDrawExtractor(
        lambda _obj: {}, lambda *_args: "id", lambda _doc: "Doc", lambda _value, default: default
    ).extract(SimpleNamespace(Objects=[page, weld, BrokenTile()]))
    assert result.records[0]["entities"][0]["stable_id"] == "Weld"
    assert result.evidence["drawing"]["failed_entity_count"] == 1
    assert any(item["code"] == "TECHDRAW_ENTITY_EXTRACTION_FAILED" for item in result.diagnostics)


def test_weld_tiles_are_assigned_to_their_parent_page() -> None:
    first = SimpleNamespace(TypeId="TechDraw::DrawWeldSymbol", Name="First", Document=None)
    second = SimpleNamespace(TypeId="TechDraw::DrawWeldSymbol", Name="Second", Document=None)
    first_tile = SimpleNamespace(
        TypeId="TechDraw::DrawTileWeld", Name="FirstTile", TileParent=first
    )
    second_tile = SimpleNamespace(
        TypeId="TechDraw::DrawTileWeld", Name="SecondTile", TileParent=second
    )
    pages = [
        SimpleNamespace(TypeId="TechDraw::DrawPage", Name="Page1", Views=[first]),
        SimpleNamespace(TypeId="TechDraw::DrawPage", Name="Page2", Views=[second]),
    ]
    result = TechDrawExtractor(
        lambda _obj: {}, lambda *_args: "id", lambda _doc: "Doc", lambda _value, default: default
    ).extract(SimpleNamespace(Objects=[*pages, first, second, first_tile, second_tile]))
    entities = {entity["stable_id"]: entity for entity in result.records[0]["entities"]}
    assert entities["FirstTile"]["page"] == 1
    assert entities["SecondTile"]["page"] == 2
