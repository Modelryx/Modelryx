import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from modelryx.engineering_search.interpreter import AISearchInterpreter, deterministic_interpret
from modelryx.engineering_search.models import AISearchRequest, SearchRequest
from modelryx.engineering_search.normalization import (
    length_to_mm,
    mass_to_kg,
    normalize_material,
)
from modelryx.engineering_search.ranking import hybrid_score
from modelryx.engineering_search.registry import SEARCH_CAPABILITIES
from pydantic import ValidationError


def interpret(query: str) -> SearchRequest:
    request, _, _ = deterministic_interpret(AISearchRequest(query=query))
    return request


def test_units_are_converted_deterministically() -> None:
    assert length_to_mm(12, "inches") == pytest.approx(304.8)
    assert length_to_mm(2, "ft") == pytest.approx(609.6)
    assert mass_to_kg(2, "lb") == pytest.approx(0.90718474)
    with pytest.raises(ValueError, match="unsupported length unit"):
        length_to_mm(2, "furlong")


def test_material_is_split_into_engineering_components() -> None:
    material = normalize_material("Aluminium 6061-T6")
    assert material.family == "aluminum"
    assert material.grade == "6061"
    assert material.temper == "T6"
    assert normalize_material("stainless steel").family == "stainless_steel"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("find part without material", "missing"),
        ("find part has no material", "missing"),
        ("find parts that do not have a material", "missing"),
        ("find parts where material is blank", "missing"),
        ("find parts with empty material", "missing"),
        ("find parts where material is not entered", "missing"),
        ("show items with assigned material", "specified"),
    ],
)
def test_material_presence_is_a_structured_filter(query: str, expected: str) -> None:
    request = interpret(query)
    assert request.filters.material_presence == expected
    assert request.text.terms == []


@pytest.mark.parametrize(
    ("query", "feature", "presence"),
    [
        ("find part has no hole", "hole", "absent"),
        ("find parts with no holes", "hole", "absent"),
        ("find parts without holes", "hole", "absent"),
        ("find hole-free parts", "hole", "absent"),
        ("find parts that contain holes", "hole", "present"),
        ("find parts with a hole", "hole", "present"),
        ("find part has no pocket", "pocket", "absent"),
        ("find parts without fillets", "fillet", "absent"),
        ("find parts with chamfers", "chamfer", "present"),
        ("find thread-free parts", "thread", "absent"),
    ],
)
def test_feature_presence_is_a_structured_filter(
    query: str, feature: str, presence: str
) -> None:
    request = interpret(query)
    assert [entry.model_dump() for entry in request.filters.feature_filters] == [
        {"feature": feature, "presence": presence}
    ]
    assert request.text.terms == []


def test_multiple_feature_presence_constraints_are_preserved() -> None:
    request = interpret("find parts with no holes and no pockets")
    assert [entry.model_dump() for entry in request.filters.feature_filters] == [
        {"feature": "hole", "presence": "absent"},
        {"feature": "pocket", "presence": "absent"},
    ]
    assert request.text.terms == []


@pytest.mark.parametrize("query", ["find parts only has one hole", "find parts with only 1 hole"])
def test_exact_feature_count_is_a_structured_filter(query: str) -> None:
    request = interpret(query)
    assert [entry.model_dump() for entry in request.filters.feature_count_filters] == [
        {"feature": "hole", "count": {"eq": 1.0, "lt": None, "lte": None, "gt": None,
         "gte": None, "target": None, "tolerance": None}}
    ]
    assert request.text.terms == []


def test_contradictory_feature_presence_is_disclosed_and_not_applied() -> None:
    request, warnings, _ = deterministic_interpret(
        AISearchRequest(query="find parts with holes and without holes")
    )
    assert request.filters.feature_filters == []
    assert any("contradictory hole presence" in warning for warning in warnings)


def test_unsupported_feature_presence_is_disclosed() -> None:
    request, warnings, _ = deterministic_interpret(
        AISearchRequest(query="find parts without slots")
    )
    assert request.filters.feature_filters == []
    assert any("slot presence is not supported" in warning for warning in warnings)


def test_invalid_ai_output_falls_back_to_correct_critical_filter() -> None:
    gateway = Mock()
    gateway.chat_enabled = True
    gateway.structured_chat.return_value = {
        "filters": {"material_presence": "invented-value"}
    }

    request, warnings, _, parser = AISearchInterpreter(gateway).interpret(
        AISearchRequest(query="find part has no material")
    )

    assert parser == "deterministic"
    assert request.filters.material_presence == "missing"
    assert request.text.terms == []
    assert any("failed validation" in warning for warning in warnings)


def test_ai_gateway_failure_falls_back_without_raising() -> None:
    gateway = Mock()
    gateway.chat_enabled = True
    gateway.structured_chat.side_effect = HTTPException(
        status_code=502, detail="AI gateway request failed: timed out"
    )

    request, warnings, _, parser = AISearchInterpreter(gateway).interpret(
        AISearchRequest(query="find parts without holes")
    )

    assert parser == "deterministic"
    assert request.filters.feature_filters[0].feature == "hole"
    assert request.filters.feature_filters[0].presence == "absent"
    assert any("deterministic parser used" in warning for warning in warnings)


def test_valid_ai_and_deterministic_interpretations_have_request_parity() -> None:
    payload = AISearchRequest(query="find part has no material")
    deterministic, _, _ = deterministic_interpret(payload)
    gateway = Mock()
    gateway.chat_enabled = True
    gateway.structured_chat.return_value = deterministic.model_dump(mode="json")

    interpreted, warnings, _, parser = AISearchInterpreter(gateway).interpret(payload)

    assert parser == "llm"
    assert warnings == []
    assert interpreted == deterministic


def test_natural_language_range_becomes_canonical_search_request() -> None:
    request = interpret("Find aluminum motor housings between 100 and 150 mm wide")
    assert request.entity_type == "part"
    assert request.text.terms == ["motor", "housings"]
    assert request.filters.material.family == "aluminum"
    assert request.filters.geometry.width_mm.gte == 100
    assert request.filters.geometry.width_mm.lte == 150


def test_inches_and_mass_comparisons_are_normalized() -> None:
    shaft = interpret("Find shafts longer than 12 inches long")
    assert shaft.filters.geometry.length_mm.gt == pytest.approx(304.8)
    housing = interpret("Find housings weighing less than 2 kg")
    assert housing.filters.mass_kg.lt == 2


def test_generic_size_uses_maximum_bounding_box_dimension() -> None:
    request = interpret("Find brackets smaller than 200 mm")
    assert request.filters.geometry.max_dimension_mm.lt == 200


def test_schema_rejects_unsupported_and_unsafe_requests() -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"limit": 1_000_000})
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"filters": {"geometry": {"width_mm": {"lt": -1}}}})
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(
            {
                "relationships": [
                    {
                        "type": "contains",
                        "target_item_id": "00000000-0000-0000-0000-000000000001",
                        "max_depth": 21,
                    }
                ]
            }
        )
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"filters": {"database_column": "secret"}})


def test_capability_registry_exposes_contract_not_database_columns() -> None:
    assert SEARCH_CAPABILITIES["canonical_units"]["length"] == "mm"
    assert SEARCH_CAPABILITIES["maximum_limit"] == 100
    assert "table" not in str(SEARCH_CAPABILITIES).casefold()


def test_phase25_hybrid_ranking_normalizes_only_active_branches() -> None:
    assert hybrid_score({"text": 0.8}) == pytest.approx(0.8)
    assert hybrid_score({"text": 0.8, "metadata": 1.0}) == pytest.approx((0.30 * 0.8 + 0.25) / 0.55)
    assert hybrid_score(
        {"text": 0.8, "vector": 0.6, "metadata": 1.0, "relationship": 0.5}
    ) == pytest.approx(0.745)
    with pytest.raises(ValueError, match="unknown ranking components"):
        hybrid_score({"database_score": 1.0})


def test_ambiguous_size_is_disclosed_not_invented() -> None:
    _, warnings, confidence = deterministic_interpret(
        AISearchRequest(query="Find large motor housings")
    )
    assert warnings == ['"large" was not applied as a numerical filter']
    assert confidence == 0.65


def _path_value(value, path: str):
    for component in path.split("."):
        value = value[int(component)] if component.isdigit() else getattr(value, component)
    return value.isoformat() if isinstance(value, datetime) else value


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parent / "search" / "phase21_queries.json").read_text()),
    ids=lambda case: case["query"],
)
def test_phase21_engineering_query_evaluation_corpus(case: dict) -> None:
    request, warnings, _ = deterministic_interpret(AISearchRequest(query=case["query"]))
    for path, expected in case["expected"].items():
        actual = _path_value(request, path)
        if isinstance(expected, float):
            assert actual == pytest.approx(expected)
        else:
            assert actual == expected
    if expected_warning := case.get("warning_contains"):
        assert any(expected_warning in warning for warning in warnings)


@pytest.mark.parametrize("system", ["example_cad", "Example CAD"])
def test_explicit_cad_system_filter_accepts_external_system_names(system: str) -> None:
    assert interpret(f'parts cad system "{system}"').filters.cad_system == system
