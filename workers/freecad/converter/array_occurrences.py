"""Produce array occurrences only from complete, explicit native element evidence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def verified_array_occurrences(
    array: dict[str, Any],
    containers: list[dict[str, Any]],
    stable_id: Callable[..., str],
    *,
    allow_partial: bool = False,
) -> list[dict[str, Any]] | None:
    """Return snapshot-indexed elements or None when their semantics are incomplete."""
    elements = array.get("elements", [])
    count = array.get("element_count")
    owned = array.get("definition_evidence", {}).get("status") == "owned_uniform"
    if (
        array.get("representation")
        not in ({"expanded", "collapsed"} if allow_partial else {"expanded"})
        or array.get("truncated")
        or array.get("world_transforms_truncated")
        or array.get("target_type") not in {"Part::Feature", "PartDesign::Body"}
        or not array.get("target")
        or not array.get("target_document")
        or array.get("definition_evidence", {}).get("status") not in {"uniform", "owned_uniform"}
        or (owned and (array.get("representation") != "expanded" or allow_partial))
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count <= 0
        or len(elements) != count
        or not containers
    ):
        return None
    if any(
        not container.get("occurrenceId")
        or not container.get("partId")
        or container.get("source", {}).get("linkedObjectName") != array["target"]
        or container.get("source", {}).get("linkedDocument") != array["target_document"]
        for container in containers
    ):
        return None
    if len({container["occurrenceId"] for container in containers}) != len(containers):
        return None
    for container in containers:
        properties = container.get("properties", {})
        if not isinstance(properties, dict):
            return None
        keys = {str(key).replace("_", "").casefold() for key in properties}
        if not owned and keys & {"configuration", "variant", "config", "modelstate"}:
            return None
        if keys & {"suppressed", "issuppressed"} and not isinstance(
            container.get("suppressed"), bool
        ):
            return None
    if [element.get("index") for element in elements] != list(range(count)):
        return None
    if len({element.get("name") for element in elements}) != count:
        return None
    for element in elements:
        suppression = element.get("suppression", {})
        if (
            not element.get("name")
            or (
                not allow_partial
                and (
                    suppression.get("source") != "native"
                    or suppression.get("state") not in {"active", "suppressed"}
                )
            )
            or (
                allow_partial
                and suppression.get("state") not in {"active", "suppressed", "unknown"}
            )
            or (
                suppression.get("state") in {"active", "suppressed"}
                and suppression.get("source") != "native"
            )
            or not isinstance(element.get("visibility"), bool)
            or element.get("transform_to_array_parent") is None
        ):
            return None
        transforms = {
            entry.get("container_occurrence_id"): entry.get("transform")
            for entry in element.get("world_transforms", [])
        }
        if any(transforms.get(container.get("occurrenceId")) is None for container in containers):
            return None

    records = []
    for container in containers:
        for element in elements:
            suppression_state = element["suppression"]["state"]
            native_suppressed = suppression_state == "suppressed"
            inherited_suppressed = container.get("suppressed") is True
            suppressed = native_suppressed or inherited_suppressed
            source = dict(container.get("source", {}))
            expanded = array["representation"] == "expanded"
            element_name = (
                element["name"] if expanded else f"{array['name']} [{element['index'] + 1}]"
            )
            source.update(
                {
                    "objectName": element["name"] if expanded else array["name"],
                    "objectLabel": element_name,
                    "typeId": "App::LinkElement" if expanded else "App::Link",
                    "arrayObjectName": array["name"],
                    "arrayIndex": element["index"],
                    "identityBasis": "array_index",
                }
            )
            records.append(
                {
                    "schemaVersion": container.get("schemaVersion", 1),
                    "nodeType": container.get("nodeType", "part"),
                    "cadType": "LinkElement" if expanded else "LinkArrayElement",
                    "partId": container["partId"],
                    "occurrenceId": stable_id(
                        "array-element", container["occurrenceId"], str(element["index"])
                    ),
                    "parentOccurrenceId": container["occurrenceId"],
                    "renderOccurrenceId": container["occurrenceId"],
                    "name": element_name,
                    "source": source,
                    "display": {},
                    "properties": dict(container.get("properties", {})),
                }
            )
            if inherited_suppressed or suppression_state in {"active", "suppressed"}:
                records[-1]["suppressed"] = suppressed
                records[-1]["suppressionSource"] = "ancestor" if inherited_suppressed else "native"
                records[-1]["display"]["visible"] = (
                    bool(container.get("display", {}).get("visible"))
                    and element["visibility"]
                    and not suppressed
                )
            elif not container.get("display", {}).get("visible") or not element["visibility"]:
                records[-1]["display"]["visible"] = False
            for field in ("material", "densityKgM3"):
                if field in container:
                    records[-1][field] = container[field]
    return records
