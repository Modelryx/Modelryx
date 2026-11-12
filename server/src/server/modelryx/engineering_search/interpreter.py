import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from modelryx.engineering_search.catalog_loader import active_scenario_catalog
from modelryx.engineering_search.models import (
    AISearchRequest,
    DatePredicate,
    FeatureCountFilter,
    FeatureFilter,
    GeometryFilters,
    MaterialFilter,
    NumericPredicate,
    SearchFilters,
    SearchRequest,
    SearchSort,
    TextSearch,
)
from modelryx.engineering_search.normalization import (
    length_to_mm,
    mass_to_kg,
    normalize_material,
)
from modelryx.engineering_search.registry import SEARCH_CAPABILITIES
from modelryx.services.ai import AiGateway

_CATALOG = active_scenario_catalog()


def _phrase_pattern(values: list[str]) -> str:
    return "|".join(
        re.escape(value).replace(r"\ ", r"\s+")
        for value in sorted(values, key=len, reverse=True)
    )


_MATERIAL_CONFIGURATION = _CATALOG.scenario("MAT-VALUE")["configuration"]
_MATERIAL_TERMS = _MATERIAL_CONFIGURATION["terms"]
_MATERIAL_SIMPLE_TERMS = [
    value for value in _MATERIAL_TERMS if value not in {"aluminum", "aluminium", "al"}
]
_MATERIAL_PATTERN = re.compile(
    rf"\b((?:aluminum|aluminium|al)(?:\s+\d{{3,4}}(?:[- ]?[A-Za-z]\d+)?)?|"
    rf"{_phrase_pattern(_MATERIAL_SIMPLE_TERMS)})\b",
    re.IGNORECASE,
)
_LENGTH_UNIT = r"mm|millimeters?|cm|centimeters?|m|meters?|in|inch(?:es)?|ft|feet|foot"
_MASS_UNIT = r"kg|kilograms?|g|grams?|lb|lbs|pounds?"
_FEATURE_CONFIGURATION = _CATALOG.scenario("FEAT-PRES")["configuration"]
_FEATURE_ALIASES = _FEATURE_CONFIGURATION["supported_aliases"]
_FEATURE_TERM = "|".join(sorted(_FEATURE_ALIASES, key=len, reverse=True))
_ABSENT_PREFIX = _phrase_pattern(_FEATURE_CONFIGURATION["absent_prefixes"])
_PRESENT_PREFIX = _phrase_pattern(_FEATURE_CONFIGURATION["present_prefixes"])
_ABSENT_FEATURE_PATTERN = re.compile(
    rf"\b(?:(?:{_ABSENT_PREFIX})\s+(?:a\s+|any\s+)?(?P<feature>{_FEATURE_TERM})|"
    rf"(?P<free_feature>{_FEATURE_TERM})[- ]free)\b",
    re.IGNORECASE,
)
_PRESENT_FEATURE_PATTERN = re.compile(
    rf"\b(?:{_PRESENT_PREFIX})\s+"
    rf"(?:a\s+|any\s+|one\s+or\s+more\s+)?(?P<feature>{_FEATURE_TERM})\b",
    re.IGNORECASE,
)
_FEATURE_COUNT_PATTERN = re.compile(
    rf"\b(?:(?:only\s+)?(?:has|have|with|contains?|includes?)\s+(?:only\s+)?)"
    rf"(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    rf"(?P<feature>{_FEATURE_TERM})\b",
    re.IGNORECASE,
)
_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_UNSUPPORTED_FEATURE_ALIASES = _FEATURE_CONFIGURATION["unsupported_aliases"]
_UNSUPPORTED_FEATURE_TERM = "|".join(
    sorted(_UNSUPPORTED_FEATURE_ALIASES, key=len, reverse=True)
)
_UNSUPPORTED_FEATURE_PATTERN = re.compile(
    rf"\b(?:{_ABSENT_PREFIX}|{_PRESENT_PREFIX})\s+"
    rf"(?:a\s+|any\s+|one\s+or\s+more\s+)?(?P<feature>{_UNSUPPORTED_FEATURE_TERM})\b",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "about",
    "after",
    "all",
    "and",
    "around",
    "assemblies",
    "assembly",
    "before",
    "between",
    "below",
    "by",
    "cad",
    "category",
    "classification",
    "classified",
    "created",
    "component",
    "components",
    "find",
    "from",
    "greater",
    "item",
    "items",
    "less",
    "made",
    "material",
    "file",
    "files",
    "missing",
    "modified",
    "model",
    "models",
    "only",
    "ones",
    "owned",
    "instead",
    "part",
    "parts",
    "failed",
    "processing",
    "ready",
    "recently",
    "revision",
    "show",
    "smaller",
    "status",
    "system",
    "that",
    "than",
    "the",
    "under",
    "without",
    "warnings",
    "weighing",
    "where",
    "with",
}


def _predicate(operator: str, value: float) -> NumericPredicate:
    return NumericPredicate(**{operator: round(value, 6)})


def _comparison(prefix: str) -> str:
    value = prefix.casefold().strip()
    if any(term in value for term in ("less", "under", "below", "smaller")):
        return "lt"
    if any(term in value for term in ("at most", "no more")):
        return "lte"
    if any(term in value for term in ("greater", "more", "over", "longer")):
        return "gt"
    if any(term in value for term in ("at least", "no less")):
        return "gte"
    return "eq"


def _extract_dimension(query: str, label: str) -> NumericPredicate | None:
    adjective = {
        "width": r"(?:wide|width)",
        "height": r"(?:high|tall|height)",
        "length": r"(?:long|length)",
        "max_dimension": r"(?:in size|maximum dimension|max dimension)",
    }[label]
    range_match = re.search(
        rf"(?:between\s+)?(\d+(?:\.\d+)?)\s*(?:-|–|to|and)\s*"
        rf"(\d+(?:\.\d+)?)\s*({_LENGTH_UNIT})\s*{adjective}",
        query,
        re.IGNORECASE,
    )
    if range_match:
        return NumericPredicate(
            gte=length_to_mm(float(range_match.group(1)), range_match.group(3)),
            lte=length_to_mm(float(range_match.group(2)), range_match.group(3)),
        )
    value_match = re.search(
        rf"((?:less|under|below|smaller|greater|more|over|longer|at least|at most|"
        rf"no more|no less)(?:\s+than)?\s+)?(\d+(?:\.\d+)?)\s*({_LENGTH_UNIT})\s*{adjective}",
        query,
        re.IGNORECASE,
    )
    if value_match:
        return _predicate(
            _comparison(value_match.group(1) or ""),
            length_to_mm(float(value_match.group(2)), value_match.group(3)),
        )
    return None


def _merge_follow_up(previous: SearchRequest, update: SearchRequest, query: str) -> SearchRequest:
    merged = previous.model_copy(deep=True)
    lowered = query.casefold()
    if re.search(r"\b(?:part|parts|assembly|assemblies|item|items)\b", lowered):
        merged.entity_type = update.entity_type
    if update.text.terms:
        merged.text = update.text
    for field in SearchFilters.model_fields:
        value = getattr(update.filters, field)
        if value not in (None, [], {}):
            if field == "geometry" and merged.filters.geometry:
                geometry = merged.filters.geometry.model_copy(deep=True)
                for geometry_field in GeometryFilters.model_fields:
                    replacement = getattr(value, geometry_field)
                    if replacement is not None:
                        setattr(geometry, geometry_field, replacement)
                merged.filters.geometry = geometry
            else:
                setattr(merged.filters, field, value)
    if update.similarity is not None:
        merged.similarity = update.similarity
    if update.relationships:
        merged.relationships = update.relationships
    merged.limit = update.limit
    merged.offset = 0
    return merged


def deterministic_interpret(
    payload: AISearchRequest, previous_request: SearchRequest | None = None
) -> tuple[SearchRequest, list[str], float]:
    query = payload.query.strip()
    lowered = query.casefold()
    entity_aliases = _CATALOG.scenario("ENT")["configuration"]["aliases"]
    entity_matches = list(
        re.finditer(rf"\b({_phrase_pattern(list(entity_aliases))})\b", lowered)
    )
    entity_type = entity_aliases[entity_matches[-1].group(1)] if entity_matches else "part"
    warnings: list[str] = []
    relationships = []
    relationship_intent = re.search(
        r"\b(?:assembl(?:y|ies)\s+containing|where[- ]used|parent assembl(?:y|ies))\b",
        lowered,
    )
    if relationship_intent:
        if payload.context.current_item_id:
            relationship_type = (
                "contains" if "containing" in relationship_intent.group(0) else "where_used"
            )
            recursive = bool(
                re.search(r"\b(?:indirectly|recursive|top-level|all levels)\b", lowered)
            )
            relationships.append(
                {
                    "type": relationship_type,
                    "target_item_id": payload.context.current_item_id,
                    "depth": "recursive" if recursive else "direct",
                }
            )
            entity_type = "assembly"
        else:
            warnings.append("relationship search was not applied without a current item")
    similarity = None
    if re.search(r"\bsimilar(?:ity)?\b|\blike this\b", lowered):
        if payload.context.current_revision_id:
            similarity = {
                "source_item_revision_id": payload.context.current_revision_id,
                "method": "metadata_embedding",
            }
        else:
            warnings.append("similarity was not applied without a current revision")

    material = None
    material_match = _MATERIAL_PATTERN.search(query)
    if material_match:
        normalized = normalize_material(material_match.group(1))
        material = MaterialFilter(**normalized.__dict__)
    material_presence = None
    material_presence_configuration = _CATALOG.scenario("MAT-PRES")["configuration"]
    missing_terms = _phrase_pattern(material_presence_configuration["missing_terms"])
    specified_terms = _phrase_pattern(material_presence_configuration["specified_terms"])
    missing_material = re.search(
        r"\b(?:(?:(?:has|have|with)\s+no|(?:does|do)\s+not\s+have(?:\s+any)?|"
        rf"without|{missing_terms})\s+"
        r"(?:a\s+|any\s+)?material|material\s+(?:is\s+)?(?:missing|blank|empty|unspecified|"
        rf"{missing_terms}))\b",
        lowered,
    )
    specified_material = re.search(
        rf"\b(?:with\s+(?:a\s+)?(?:{specified_terms})\s+material"
        rf"|material\s+(?:is\s+)?(?:{specified_terms}))\b",
        lowered,
    )
    if missing_material:
        material_presence = "missing"
    elif specified_material:
        material_presence = "specified"
    absent_features = list(_ABSENT_FEATURE_PATTERN.finditer(query))
    feature_count_matches = list(_FEATURE_COUNT_PATTERN.finditer(query))
    present_features = [
        match
        for match in _PRESENT_FEATURE_PATTERN.finditer(query)
        if not any(
            match.start() < absent.end() and absent.start() < match.end()
            for absent in absent_features
        )
    ]
    feature_filters = []
    feature_count_filters = []
    for match in feature_count_matches:
        raw_count = match.group("count").casefold()
        count = _COUNT_WORDS.get(raw_count, int(raw_count) if raw_count.isdigit() else 0)
        feature_count_filters.append(
            FeatureCountFilter(
                feature=_FEATURE_ALIASES[match.group("feature").casefold()],
                count=NumericPredicate(eq=count),
            )
        )
    feature_presence: dict[str, str] = {}
    for feature_match, presence in (
        *((match, "absent") for match in absent_features),
        *((match, "present") for match in present_features),
    ):
        raw_feature = feature_match.group("feature") or feature_match.groupdict().get(
            "free_feature"
        )
        feature = _FEATURE_ALIASES[raw_feature.casefold()]
        previous_presence = feature_presence.get(feature)
        if previous_presence and previous_presence != presence:
            warnings.append(
                f"contradictory {feature} presence was not applied; clarify whether it "
                "must be present or absent"
            )
            feature_filters = [entry for entry in feature_filters if entry.feature != feature]
            feature_presence[feature] = "conflict"
        elif previous_presence is None:
            feature_filters.append(FeatureFilter(feature=feature, presence=presence))
            feature_presence[feature] = presence
    unsupported_features = {
        _UNSUPPORTED_FEATURE_ALIASES[match.group("feature").casefold()]
        for match in _UNSUPPORTED_FEATURE_PATTERN.finditer(query)
    }
    for unsupported_feature in sorted(unsupported_features):
        warnings.append(
            f"{unsupported_feature} presence is not supported by the current CAD metadata extractor"
        )

    geometry_values: dict[str, NumericPredicate] = {}
    for label, field in (
        ("width", "width_mm"),
        ("height", "height_mm"),
        ("length", "length_mm"),
        ("max_dimension", "max_dimension_mm"),
    ):
        predicate = _extract_dimension(query, label)
        if predicate:
            geometry_values[field] = predicate

    if not geometry_values:
        generic = re.search(
            rf"((?:less|under|below|smaller|greater|more|over|at least|at most)"
            rf"(?:\s+than)?\s+)(\d+(?:\.\d+)?)\s*({_LENGTH_UNIT})\b",
            query,
            re.IGNORECASE,
        )
        if generic:
            geometry_values["max_dimension_mm"] = _predicate(
                _comparison(generic.group(1)),
                length_to_mm(float(generic.group(2)), generic.group(3)),
            )

    mass = None
    mass_match = re.search(
        rf"((?:less|under|below|greater|more|over|at least|at most)(?:\s+than)?\s+)"
        rf"(\d+(?:\.\d+)?)\s*({_MASS_UNIT})\b",
        query,
        re.IGNORECASE,
    )
    if mass_match:
        mass = _predicate(
            _comparison(mass_match.group(1)),
            mass_to_kg(float(mass_match.group(2)), mass_match.group(3)),
        )

    state_configuration = _CATALOG.scenario("STATE")["configuration"]
    status_match = re.search(
        rf"\b({_phrase_pattern(list(state_configuration['processing_aliases']))})\b",
        lowered,
    )
    status = (
        state_configuration["processing_aliases"][status_match.group(1)]
        if status_match
        else None
    )
    lifecycle_match = re.search(
        rf"\b({_phrase_pattern(list(state_configuration['lifecycle_aliases']))})\b",
        lowered,
    )
    lifecycle = (
        state_configuration["lifecycle_aliases"][lifecycle_match.group(1)]
        if lifecycle_match
        else None
    )
    revision_match = re.search(r"\brevision\s+([A-Za-z0-9._-]+)\b", query, re.IGNORECASE)
    identifiers = re.findall(r"\b[A-Z]{1,8}-\d{2,}\b", query)
    classification_match = re.search(
        r"\b(?:category|classified as|classification)\s+([A-Za-z][A-Za-z0-9_-]*)",
        query,
        re.IGNORECASE,
    )
    cad_configuration = _CATALOG.scenario("CAD-FORMAT")["configuration"]
    cad_match = re.search(
        rf"\b(?:cad system|from)\s+({_phrase_pattern(cad_configuration['cad_systems'])})\b",
        query,
        re.IGNORECASE,
    )
    format_aliases = cad_configuration["format_aliases"]
    format_terms = _phrase_pattern(list(format_aliases))
    artifact_format_match = re.search(
        rf"\b(?:(?P<format>{format_terms})\s+(?:files?|artifacts?|models?|parts?|items?|"
        rf"assemblies)|(?:files?|artifacts?|models?)\s+(?:in\s+)?(?P<after>{format_terms}))\b",
        lowered,
    )
    artifact_format = None
    if artifact_format_match:
        artifact_format = format_aliases[
            artifact_format_match.group("format") or artifact_format_match.group("after")
        ]
    owner_match = re.search(
        r"\b(?:owned|created) by\s+([A-Za-z][A-Za-z .'-]*?)(?=\s+(?:created|modified|"
        r"after|before|with|revision|status)\b|[,.]|$)",
        query,
        re.IGNORECASE,
    )
    self_owner_match = re.search(
        r"\bmy\s+(?:parts?|items?|components?|assembl(?:y|ies))\b"
        r"|\b(?:owned|created)\s+by\s+me\b",
        lowered,
    )
    date_filters: dict[str, DatePredicate] = {}
    for date_field in ("created", "modified"):
        match = re.search(
            rf"\b{date_field}\s+(after|before)\s+(\d{{4}}-\d{{2}}-\d{{2}})\b",
            query,
            re.IGNORECASE,
        )
        if match:
            parsed_date = datetime.fromisoformat(match.group(2)).replace(tzinfo=UTC)
            date_filters[date_field] = DatePredicate(**{match.group(1).casefold(): parsed_date})
        relative = re.search(
            rf"\b{date_field}\s+(?:in\s+)?(?:the\s+)?last\s+(\d{{1,3}})\s+days?\b",
            lowered,
        )
        now = datetime.now(UTC)
        if relative:
            date_filters[date_field] = DatePredicate(
                after=now - timedelta(days=int(relative.group(1)))
            )
        elif re.search(rf"\b{date_field}\s+today\b", lowered):
            date_filters[date_field] = DatePredicate(
                after=now.replace(hour=0, minute=0, second=0, microsecond=0)
            )
        elif re.search(rf"\b{date_field}\s+yesterday\b", lowered):
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            date_filters[date_field] = DatePredicate(
                after=today - timedelta(days=1), before=today
            )
    if "recently" in lowered and "modified" in lowered and "modified" not in date_filters:
        warnings.append('"recently" was not applied without an explicit date')

    approval_state = None
    if re.search(r"\b(?:awaiting|pending) (?:approval|review)\b", lowered):
        approval_state = "PENDING"
    elif re.search(r"\brejected (?:changes|reviews?|parts?|items?)\b", lowered):
        approval_state = "REJECTED"
    approval_assigned_to_me = bool(
        re.search(r"\b(?:what do i need to review|my (?:reviews?|review inbox))\b", lowered)
    )
    if approval_assigned_to_me:
        approval_state = "PENDING"
    approval_due_before = None
    if re.search(r"\boverdue (?:reviews?|approvals?)\b", lowered):
        approval_state = "PENDING"
        approval_due_before = datetime.now(UTC)
    checkout_owner = (
        "$me" if re.search(r"\bchecked out by me\b|\bmy checked out items?\b", lowered) else None
    )
    artifact_presence = None
    artifact_missing = None
    artifact_coverage_match = re.search(
        r"\b(?:without|missing|no) (drawings?|previews?|native (?:cad|files?))\b", lowered
    )
    if artifact_coverage_match:
        artifact_presence = {
            "drawing": "drawing", "drawings": "drawing",
            "preview": "preview", "previews": "preview",
            "native cad": "native_cad", "native file": "native_cad", "native files": "native_cad",
        }[artifact_coverage_match.group(1)]
        artifact_missing = True
    extraction_status = (
        "failed" if re.search(r"\b(?:failed|invalid) (?:metadata )?extraction\b", lowered) else None
    )
    has_warnings = True if re.search(r"\bwith (?:processing )?warnings\b", lowered) else None

    sort = SearchSort()
    if re.search(r"\bnewest\b", lowered):
        sort = SearchSort(field="created", direction="desc")
    elif re.search(r"\boldest\b", lowered):
        sort = SearchSort(field="created", direction="asc")
    else:
        sort_configuration = _CATALOG.scenario("SORT")["configuration"]
        sort_match = re.search(
            rf"\bsort(?:ed)?\s+by\s+({_phrase_pattern(list(sort_configuration['field_aliases']))})"
            r"(?:\s+(ascending|descending|asc|desc))?\b",
            lowered,
        )
        if sort_match:
            sort = SearchSort(
                field=sort_configuration["field_aliases"][sort_match.group(1)],
                direction=(
                    "asc" if sort_match.group(2) in {"ascending", "asc"} else "desc"
                ),
            )
    limit = payload.limit
    limit_match = re.search(r"\b(?:top|first|newest|oldest)\s+(\d{1,4})\b", lowered)
    if limit_match:
        requested_limit = int(limit_match.group(1))
        limit = min(requested_limit, SEARCH_CAPABILITIES["maximum_limit"])
        if requested_limit > SEARCH_CAPABILITIES["maximum_limit"]:
            warnings.append("result limit was capped at 100")

    removed = _MATERIAL_PATTERN.sub(" ", lowered)
    for material_presence_match in (missing_material, specified_material):
        if material_presence_match:
            removed = removed.replace(material_presence_match.group(0), " ")
    for feature_match in (*absent_features, *present_features, *feature_count_matches):
        if feature_match:
            removed = removed.replace(feature_match.group(0).casefold(), " ")
    removed = re.sub(
        r"\b(?:find|show)?\s*(?:parts?|items?)?\s*similar(?:ity)?\s*(?:to)?\s*(?:this)?\b",
        " ",
        removed,
    )
    removed = re.sub(
        r"\b(?:show|find)?\s*(?:all\s+)?(?:top-level\s+)?(?:assembl(?:y|ies)\s+containing"
        r"|where[- ]used|parent assembl(?:y|ies))(?:\s+(?:this|item|part|component|bearing))?\b",
        " ",
        removed,
    )
    for interpreted_match in (
        classification_match,
        cad_match,
        artifact_format_match,
        self_owner_match,
        owner_match,
        revision_match,
        status_match,
        lifecycle_match,
        limit_match,
        artifact_coverage_match,
    ):
        if interpreted_match:
            removed = removed.replace(interpreted_match.group(0).casefold(), " ")
    for date_field in ("created", "modified"):
        date_clause = re.search(
            rf"\b{date_field}\s+(?:(?:after|before)\s+\d{{4}}-\d{{2}}-\d{{2}}|"
            rf"(?:in\s+)?(?:the\s+)?last\s+\d{{1,3}}\s+days?|today|yesterday)\b",
            removed,
            re.IGNORECASE,
        )
        if date_clause:
            removed = removed.replace(date_clause.group(0).casefold(), " ")
    removed = re.sub(rf"\b\d+(?:\.\d+)?\s*(?:{_LENGTH_UNIT}|{_MASS_UNIT})\b", " ", removed)
    removed = re.sub(
        r"\bsort(?:ed)?\s+by\s+(?:part number|item number|name|created|modified)"
        r"(?:\s+(?:ascending|descending|asc|desc))?\b",
        " ",
        removed,
    )
    removed = re.sub(r"\b(?:newest|oldest)\b", " ", removed)
    removed = re.sub(
        r"\b(?:awaiting|pending) (?:approval|review)|rejected (?:changes|reviews?|parts?|items?)"
        r"|what do i need to review|my (?:reviews?|review inbox)|overdue (?:reviews?|approvals?)"
        r"|checked out by me|my checked out items?|(?:failed|invalid) (?:metadata )?extraction"
        r"|with (?:processing )?warnings\b",
        " ",
        removed,
    )
    words = re.findall(r"[a-z][a-z0-9_-]+", removed)
    terms = []
    for word in words:
        if word not in _STOP_WORDS and word not in {
            "wide",
            "width",
            "high",
            "height",
            "long",
            "length",
        }:
            if word not in terms and not any(
                word in identifier.casefold() for identifier in identifiers
            ):
                terms.append(word)

    if "large" in terms:
        terms.remove("large")
        warnings.append('"large" was not applied as a numerical filter')
    request = SearchRequest(
        entity_type=entity_type,
        text=TextSearch(terms=terms[:20]),
        filters=SearchFilters(
            identifiers=identifiers,
            classification=classification_match.group(1) if classification_match else None,
            material=material,
            material_presence=material_presence,
            feature_filters=feature_filters,
            feature_count_filters=feature_count_filters,
            geometry=GeometryFilters(**geometry_values) if geometry_values else None,
            mass_kg=mass,
            cad_system=cad_match.group(1) if cad_match else None,
            artifact_format=artifact_format,
            revision=revision_match.group(1) if revision_match else None,
            status=status,
            lifecycle=lifecycle,
            owner=(
                "$me"
                if self_owner_match or (owner_match and owner_match.group(1).strip() == "me")
                else owner_match.group(1).strip() if owner_match else None
            ),
            created=date_filters.get("created"),
            modified=date_filters.get("modified"),
            approval_state=approval_state,
            approval_assigned_to_me=approval_assigned_to_me or None,
            approval_due_before=approval_due_before,
            checkout_owner=checkout_owner,
            artifact_presence=artifact_presence,
            artifact_missing=artifact_missing,
            extraction_status=extraction_status,
            has_warnings=has_warnings,
        ),
        similarity=similarity,
        relationships=relationships,
        sort=[sort],
        limit=limit,
    )
    if previous_request is not None:
        request = _merge_follow_up(previous_request, request, query)
        warnings.append("follow-up applied to the previous validated search")
    confidence = (
        0.9
        if any(
            (
                material,
                material_presence,
                feature_filters,
                feature_count_filters,
                geometry_values,
                mass,
                identifiers,
                similarity,
                relationships,
            )
        )
        else 0.65
    )
    return request, warnings, confidence


class AISearchInterpreter:
    def __init__(self, gateway: AiGateway):
        self.gateway = gateway

    def interpret(
        self, payload: AISearchRequest, previous_request: SearchRequest | None = None
    ) -> tuple[SearchRequest, list[str], float, str]:
        if not self.gateway.chat_enabled:
            request, warnings, confidence = deterministic_interpret(payload, previous_request)
            warnings.append("AI gateway is not configured; deterministic engineering parser used")
            return request, warnings, confidence, "deterministic"
        system = (
            "Translate engineering search language into the supplied Modelryx SearchRequest JSON "
            "schema. Never emit SQL. Use only capabilities supplied in the payload. Preserve all "
            "quantities in canonical units: length mm and mass kg. Do not guess numeric meanings "
            "for subjective words. BOM relationships require a target item from the supplied "
            "context. Use material_presence='missing' for Items without a recorded material and "
            "material_presence='specified' when a recorded material is required. "
            "Use filters.feature_filters with a supported feature and presence='present' or "
            "'absent' for queries about successfully extracted CAD feature counts. "
            "Use filters.feature_count_filters with an exact or comparative count when the user "
            "specifies how many features a part must have; 'only one hole' means count.eq=1. "
            "context. Similarity requires metadata_embedding and a source revision. When a "
            "previous request is supplied, return the complete updated request after applying "
            "the follow-up."
        )
        try:
            raw: dict[str, Any] = self.gateway.structured_chat(
                system=system,
                payload={
                    "query": payload.query,
                    "context": payload.context.model_dump(mode="json"),
                    "previous_request": (
                        previous_request.model_dump(mode="json") if previous_request else None
                    ),
                    "capabilities": SEARCH_CAPABILITIES,
                    "scenario_catalog": _CATALOG.public(),
                },
                schema=SearchRequest.model_json_schema(),
                schema_name="modelryx_search_request",
            )
            request = SearchRequest.model_validate(raw)
            request.limit = payload.limit
            return request, [], 0.95, "llm"
        except (HTTPException, ValidationError, ValueError):
            request, warnings, confidence = deterministic_interpret(payload, previous_request)
            warnings.append("AI interpretation failed validation; deterministic parser used")
            return request, warnings, confidence, "deterministic"
