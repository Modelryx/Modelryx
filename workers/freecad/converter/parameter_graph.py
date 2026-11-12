"""Conservative, deterministic dependency evidence from extracted FreeCAD parameters."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from extraction_provider import ProviderResult

_RESERVED = {
    "abs",
    "acos",
    "asin",
    "atan",
    "cos",
    "deg",
    "exp",
    "false",
    "if",
    "log",
    "max",
    "min",
    "mm",
    "pi",
    "rad",
    "sin",
    "sqrt",
    "tan",
    "true",
}
_TOKEN = re.compile(r"(?<![\w])([A-Za-z_][\w]*(?:\[\d+\])?(?:\.[A-Za-z_][\w]*(?:\[\d+\])?)*)")
_QUOTED = re.compile(r'<<.*?>>|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
_LABEL_REFERENCE = re.compile(
    r"<<([^<>]+)>>\.([A-Za-z_][\w]*(?:\[\d+\])?(?:\.[A-Za-z_][\w]*(?:\[\d+\])?)*)"
)
_CROSS_DOCUMENT = re.compile(
    r"(?:[A-Za-z_][\w]*|<<[^<>]+>>)#(?:<<[^<>]+>>|[A-Za-z_][\w]*)"
    r"(?:\.[A-Za-z_][\w]*)*"
)
_CROSS_PARTS = re.compile(
    r"^(?P<doc>[A-Za-z_][\w]*|<<[^<>]+>>)#"
    r"(?P<object>[A-Za-z_][\w]*|<<[^<>]+>>)\.(?P<property>[A-Za-z_][\w.]*)$"
)


class BoundedParameterLoader:
    """Share a hard document extraction budget across external graph passes."""

    def __init__(self, extract_native: Any, *, max_documents: int = 100):
        self.extract_native = extract_native
        self.max_documents = max_documents
        self.cache: dict[int, tuple[Any, tuple[list, list, list]]] = {}
        self.limit_reached = False

    def __call__(self, document: Any) -> tuple[list, list, list]:
        key = id(document)
        cached = self.cache.get(key)
        if cached is not None and cached[0] is document:
            return cached[1]
        if len(self.cache) >= self.max_documents:
            self.limit_reached = True
            return [], [], [{"property": "document_limit"}]
        try:
            result = self.extract_native(document)
        except (AttributeError, RuntimeError, TypeError, ValueError, OSError):
            result = ([], [], [{"property": "document_extraction_failed"}])
        self.cache[key] = (document, result)
        return result


def external_parameter_evidence(
    document: Any, graph: dict[str, Any], extract_native: Any, identity: Any
) -> list[dict[str, Any]]:
    """Resolve only linked-document aliases supported by native OutList evidence."""
    documents_by_owner: dict[str, dict[str, list[Any]]] = {}
    unreadable_owners: set[str] = set()
    for obj in getattr(document, "Objects", ()):
        owner_name = str(getattr(obj, "Name", ""))
        documents = documents_by_owner.setdefault(owner_name, {})
        try:
            links = getattr(obj, "OutList", ()) or ()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            unreadable_owners.add(owner_name)
            continue
        for linked in links:
            try:
                other = getattr(linked, "Document", None)
                if other is None or other is document:
                    continue
                for key in {str(getattr(other, "Name", "")), str(getattr(other, "Label", ""))}:
                    if key and all(existing is not other for existing in documents.setdefault(key, [])):
                        documents[key].append(other)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                unreadable_owners.add(owner_name)
                continue
    cache: dict[int, tuple[list[dict[str, Any]], list[dict[str, str]]]] = {}
    evidence = []
    for node in graph["nodes"]:
        documents = documents_by_owner.get(str(node.get("feature_id") or ""), {})
        expression = str(node.get("expression") or "")
        expression = re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', " ", expression)
        for match in _CROSS_DOCUMENT.finditer(expression):
            raw = match.group(0)
            parts = _CROSS_PARTS.fullmatch(raw)
            record = {"target": node["id"], "reference": raw, "status": "unresolved"}
            if parts and str(node.get("feature_id") or "") not in unreadable_owners:
                doc_name = parts["doc"].removeprefix("<<").removesuffix(">>")
                candidates = documents.get(doc_name, [])
                if len(candidates) > 1:
                    record["status"] = "ambiguous"
                elif len(candidates) == 1:
                    remote = candidates[0]
                    object_name = parts["object"].removeprefix("<<").removesuffix(">>")
                    by_label = parts["object"].startswith("<<")
                    try:
                        objects = [obj for obj in getattr(remote, "Objects", ())
                                   if str(getattr(obj, "Label" if by_label else "Name", "")) == object_name]
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        objects = []
                    if len(objects) > 1:
                        record["status"] = "ambiguous"
                    elif len(objects) == 1:
                        if id(remote) not in cache:
                            try:
                                params, _, skipped = extract_native(remote)
                                cache[id(remote)] = (params, skipped)
                            except (AttributeError, RuntimeError, TypeError, ValueError):
                                cache[id(remote)] = ([], [{"property": "document"}])
                        params, skipped = cache[id(remote)]
                        obj_name = str(objects[0].Name)
                        candidates_params = [p for p in params if p.get("feature_id") == obj_name
                                             and (p.get("name") == parts["property"]
                                                  or p.get("native_name") == parts["property"]
                                                  or p.get("name") == f'{obj_name}.{parts["property"]}')]
                        if len(candidates_params) > 1:
                            record["status"] = "ambiguous"
                        elif len(candidates_params) == 1 and not skipped:
                            param = candidates_params[0]
                            try:
                                doc_identity = identity(remote)
                            except (AttributeError, RuntimeError, TypeError, ValueError, OSError):
                                doc_identity = None
                            if doc_identity:
                                record.update(status="resolved", source_document=doc_identity,
                                              source_object=obj_name, source_parameter=param["native_name"],
                                              value=param.get("value"), unit=param.get("unit"),
                                              source_expression=param.get("expression"))
            evidence.append(record)
    return sorted(evidence, key=lambda item: (str(item["target"]), str(item["reference"])))


def add_external_graph_edges(graph: dict[str, Any], document_identity: str) -> None:
    """Add explicitly scoped direct edges without changing legacy local node IDs."""
    nodes: dict[tuple[str, str, str], dict[str, Any]] = {}
    edges: set[tuple[str, str, str, str, str, str]] = set()
    for item in graph.get("external_references", []):
        if item.get("status") != "resolved":
            continue
        key = (item["source_document"], item["source_object"], item["source_parameter"])
        nodes[key] = {
            "document": key[0], "object": key[1], "parameter": key[2],
            "value": item.get("value"), "unit": item.get("unit"),
            "expression": item.get("source_expression"),
        }
        edges.add((*key, document_identity, item["target"], item["reference"]))
    graph["external_nodes"] = [nodes[key] for key in sorted(nodes)]
    graph["external_edges"] = [
        {"source": {"document": doc, "object": obj, "parameter": parameter},
         "target": {"document": target_doc, "node_id": target}, "reference": reference}
        for doc, obj, parameter, target_doc, target, reference in sorted(edges)
    ]
    # External upstream expressions have not been traversed; never claim cycle completeness.
    graph["cross_document_cycles_status"] = (
        "partial" if any("#" in str(node.get("expression") or "") for node in graph["nodes"])
        else "not_applicable"
    )


def external_upstream_graph(
    document: Any, evidence: list[dict[str, Any]], extract_native: Any, identity: Any,
    *, max_nodes: int = 10000, max_documents: int = 100,
) -> dict[str, Any]:
    """Trace local upstream edges within directly linked documents, with a fixed limit."""
    if not evidence:
        return {"nodes": [], "edges": [], "cross_document_edges": [],
                "unresolved_cross_document": [], "truncated": False, "status": "not_applicable"}
    documents: dict[str, list[Any]] = {}
    for owner in getattr(document, "Objects", ()):
        try:
            linked_objects = getattr(owner, "OutList", ()) or ()
            for obj in linked_objects:
                other = getattr(obj, "Document", None)
                if other is None or other is document:
                    continue
                key = identity(other)
                group = documents.setdefault(key, [])
                if all(existing is not other for existing in group):
                    group.append(other)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    wanted: dict[str, set[str]] = {}
    partial = any(item.get("status") != "resolved" for item in evidence)
    for item in evidence:
        if item.get("status") != "resolved":
            continue
        native = str(item["source_parameter"])
        obj = str(item["source_object"])
        node_id = native if native.startswith(f"{obj}.") else f"{obj}.{native}"
        wanted.setdefault(item["source_document"], set()).add(node_id)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    cross_edges: set[tuple[str, str, str, str, str]] = set()
    unresolved_cross: list[dict[str, Any]] = []
    truncated = False
    processed: dict[str, set[str]] = {}
    while wanted:
        doc_id = min(wanted)
        targets = wanted.pop(doc_id) - processed.get(doc_id, set())
        if not targets:
            continue
        if doc_id not in processed and len(processed) >= max_documents:
            truncated = partial = True
            break
        processed.setdefault(doc_id, set()).update(targets)
        matches = documents.get(doc_id, [])
        if len(matches) != 1:
            partial = True
            continue
        try:
            remote = matches[0]
            parameters, _, skipped = extract_native(remote)
            labels: dict[str, list[str]] = {}
            for obj in getattr(remote, "Objects", ()):
                label, name = str(getattr(obj, "Label", "") or ""), str(getattr(obj, "Name", "") or "")
                if label and name:
                    labels.setdefault(label, []).append(name)
            local = parameter_dependency_graph(parameters, object_labels=labels)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            partial = True
            continue
        by_id = {node["id"]: node for node in local["nodes"]}
        incoming: dict[str, list[dict[str, str]]] = {}
        for edge in local["edges"]:
            incoming.setdefault(edge["target"], []).append(edge)
        pending = sorted(targets, reverse=True)
        seen: set[str] = {n["id"] for n in nodes if n["document"] == doc_id}
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            if len(nodes) >= max_nodes:
                truncated = partial = True
                break
            if current not in by_id:
                partial = True
                continue
            seen.add(current)
            nodes.append({"document": doc_id, **by_id[current]})
            pending.extend(edge["source"] for edge in incoming.get(current, []))
        for edge in local["edges"]:
            item = {"document": doc_id, **edge}
            if edge["source"] in seen and edge["target"] in seen:
                edges.append(item)
        try:
            for owner in getattr(remote, "Objects", ()):
                for linked in getattr(owner, "OutList", ()) or ():
                    other = getattr(linked, "Document", None)
                    if other is None or other is remote:
                        continue
                    key = identity(other)
                    group = documents.setdefault(key, [])
                    if all(existing is not other for existing in group):
                        group.append(other)
            external = external_parameter_evidence(
                remote, {"nodes": [by_id[node] for node in sorted(seen)]}, extract_native, identity
            )
            for ref in external:
                if ref["status"] != "resolved":
                    unresolved_cross.append({"document": doc_id, **ref})
                    partial = True
                    continue
                source_doc, obj, native = ref["source_document"], ref["source_object"], ref["source_parameter"]
                source = native if native.startswith(f"{obj}.") else f"{obj}.{native}"
                cross_edges.add((source_doc, source, doc_id, ref["target"], ref["reference"]))
                if source not in processed.get(source_doc, set()):
                    wanted.setdefault(source_doc, set()).add(source)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            partial = True
        if (skipped or any(item["target"] in seen for item in local["unresolved_references"])
                or any(item["target"] in seen for item in local["ambiguous_references"])
                or any(item in seen for item in local["unsupported_expressions"])
                or any(any(node in seen for node in cycle) for cycle in local["cycles"])):
            partial = True
    unique_edges = {(e["document"], e["source"], e["target"], e["reference"]): e for e in edges}
    return {"nodes": sorted(nodes, key=lambda n: (n["document"], n["id"])),
            "edges": [unique_edges[key] for key in sorted(unique_edges)],
            "cross_document_edges": [
                {"source": {"document": sd, "node_id": sn},
                 "target": {"document": td, "node_id": tn}, "reference": ref}
                for sd, sn, td, tn, ref in sorted(cross_edges)],
            "unresolved_cross_document": sorted(unresolved_cross, key=lambda e: (e["document"], e["target"], e["reference"])),
            "truncated": truncated, "status": "partial" if partial else "complete" if processed else "not_applicable"}


def cross_document_cycles(graph: dict[str, Any], root_document: str) -> list[list[dict[str, str]]]:
    """Find observed cross-document cycles; missing edges still require partial status."""
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {}
    def add(source: tuple[str, str], target: tuple[str, str]) -> None:
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set())

    for edge in graph.get("edges", []):
        add((root_document, edge["source"]), (root_document, edge["target"]))
    for edge in graph.get("external_edges", []):
        source = edge["source"]
        native, obj = source["parameter"], source["object"]
        source_id = native if native.startswith(f"{obj}.") else f"{obj}.{native}"
        add((source["document"], source_id), (edge["target"]["document"], edge["target"]["node_id"]))
    upstream = graph.get("external_upstream", {})
    for edge in upstream.get("edges", []):
        add((edge["document"], edge["source"]), (edge["document"], edge["target"]))
    for edge in upstream.get("cross_document_edges", []):
        add((edge["source"]["document"], edge["source"]["node_id"]),
            (edge["target"]["document"], edge["target"]["node_id"]))
    # Strongly connected components reveal cross-document cycles even when a
    # depth-first back edge happens to close a smaller, local-only cycle.
    visited: set[tuple[str, str]] = set()
    finish: list[tuple[str, str]] = []
    reverse: dict[tuple[str, str], set[tuple[str, str]]] = {key: set() for key in adjacency}
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].add(source)
    for start in sorted(adjacency):
        if start in visited:
            continue
        visited.add(start)
        stack = [(start, iter(sorted(adjacency[start])))]
        while stack:
            node, children = stack[-1]
            target = next(children, None)
            if target is None:
                finish.append(node)
                stack.pop()
            elif target not in visited:
                visited.add(target)
                stack.append((target, iter(sorted(adjacency[target]))))
    visited.clear()
    cycles: set[tuple[tuple[str, str], ...]] = set()
    for start in reversed(finish):
        if start in visited:
            continue
        component = {start}
        visited.add(start)
        pending = [start]
        while pending:
            for parent in sorted(reverse[pending.pop()]):
                if parent not in visited:
                    visited.add(parent)
                    component.add(parent)
                    pending.append(parent)
        if len({node[0] for node in component}) < 2:
            continue
        source, target = min((source, target) for source in component
                             for target in adjacency[source]
                             if target in component and source[0] != target[0])
        previous: dict[tuple[str, str], tuple[str, str] | None] = {target: None}
        queue = [target]
        for node in queue:
            if node == source:
                break
            for child in sorted(adjacency[node] & component):
                if child not in previous:
                    previous[child] = node
                    queue.append(child)
        path = []
        cursor: tuple[str, str] | None = source
        while cursor is not None:
            path.append(cursor)
            cursor = previous[cursor]
        ring = [source, *reversed(path[1:])]
        index = ring.index(min(ring))
        cycles.add(tuple(ring[index:] + ring[:index]))
    return [[{"document": doc, "node_id": node} for doc, node in cycle] for cycle in sorted(cycles)]


def parameter_dependency_graph(
    parameters: list[dict[str, Any]], *, object_labels: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    qualified: dict[str, set[str]] = {}
    local: dict[tuple[str, str], set[str]] = {}
    for parameter in parameters:
        scope = str(parameter.get("feature_id") or "")
        native = str(parameter.get("native_name") or "")
        name = str(parameter.get("name") or "")
        node_id = (
            native if scope and native.startswith(f"{scope}.") else f"{scope}.{native}".strip(".")
        ) or name
        nodes.append({"id": node_id, **parameter})
        aliases = {node_id, name}
        if "." not in native or (scope and native.startswith(f"{scope}.")):
            aliases.add(native)
        if scope and name and "." not in name:
            aliases.add(f"{scope}.{name}")
        for alias in aliases:
            if "." in alias:
                qualified.setdefault(alias.casefold(), set()).add(node_id)
            elif alias:
                local.setdefault((scope.casefold(), alias.casefold()), set()).add(node_id)
        if scope and native.startswith(f"{scope}."):
            local.setdefault((scope.casefold(), native[len(scope) + 1 :].casefold()), set()).add(
                node_id
            )

    edges: set[tuple[str, str, str]] = set()
    unresolved: set[tuple[str, str]] = set()
    ambiguous: set[tuple[str, str]] = set()
    unsupported: set[str] = set()
    labels: dict[str, set[str]] = {}
    for name, values in (object_labels or {}).items():
        labels.setdefault(name.casefold(), set()).update(values)
    for node in nodes:
        expression = str(node.get("expression") or "").lstrip("=").strip()
        if not expression:
            continue
        target = str(node["id"])
        scope = str(node.get("feature_id") or "").casefold()
        if "#" in expression:
            unsupported.add(target)
        parsed = _CROSS_DOCUMENT.sub(" ", expression)
        for match in _LABEL_REFERENCE.finditer(parsed):
            label, property_path = match.groups()
            reference = match.group(0)
            objects = labels.get(label.casefold(), [])
            if len(objects) > 1:
                ambiguous.add((target, reference))
            elif len(objects) == 1:
                object_name = next(iter(objects))
                candidates = qualified.get(f"{object_name}.{property_path}".casefold(), set())
                if len(candidates) == 1:
                    edges.add((next(iter(candidates)), target, reference))
                elif len(candidates) > 1:
                    ambiguous.add((target, reference))
                else:
                    unresolved.add((target, reference))
            else:
                unresolved.add((target, reference))
        parsed = _LABEL_REFERENCE.sub(" ", parsed)
        if _QUOTED.search(parsed):
            unsupported.add(target)
        parsed = _QUOTED.sub(" ", parsed)
        for token in _TOKEN.findall(parsed):
            if token.casefold() in _RESERVED:
                continue
            if "." in token:
                candidates = qualified.get(f"{scope}.{token}".casefold(), set())
                if not candidates:
                    candidates = qualified.get(token.casefold(), set())
            else:
                candidates = local.get((scope, token.casefold()), set())
            if len(candidates) == 1:
                edges.add((next(iter(candidates)), target, token))
            elif len(candidates) > 1:
                ambiguous.add((target, token))
            else:
                unresolved.add((target, token))

    adjacency: dict[str, list[str]] = {}
    for source, target, _ in edges:
        adjacency.setdefault(source, []).append(target)
    states: dict[str, int] = {}
    cycles: set[tuple[str, ...]] = set()
    for start in sorted((str(node["id"]) for node in nodes), key=str.casefold):
        if states.get(start):
            continue
        states[start] = 1
        path = [start]
        stack = [iter(sorted(adjacency.get(start, []), key=str.casefold))]
        while stack:
            target = next(stack[-1], None)
            if target is None:
                states[path.pop()] = 2
                stack.pop()
            elif states.get(target) == 1:
                ring = path[path.index(target) :]
                index = ring.index(min(ring))
                cycles.add(tuple(ring[index:] + ring[:index]))
            elif not states.get(target):
                states[target] = 1
                path.append(target)
                stack.append(iter(sorted(adjacency.get(target, []), key=str.casefold)))
    downstream = {target for _, target, _ in edges}
    upstream = {source for source, _, _ in edges}
    ordered = lambda values: sorted(values, key=lambda item: tuple(map(str.casefold, item)))
    return {
        "nodes": sorted(nodes, key=lambda item: str(item["id"]).casefold()),
        "edges": [
            {"source": source, "target": target, "reference": ref}
            for source, target, ref in ordered(edges)
        ],
        "cycles": [list(cycle) for cycle in sorted(cycles)],
        "unresolved_references": [
            {"target": target, "reference": ref} for target, ref in ordered(unresolved)
        ],
        "ambiguous_references": [
            {"target": target, "reference": ref} for target, ref in ordered(ambiguous)
        ],
        "unsupported_expressions": sorted(unsupported, key=str.casefold),
        "key_parameters": sorted(upstream - downstream, key=str.casefold),
        "driven_parameters": sorted(downstream, key=str.casefold),
    }


def native_parameters(
    document: Any, to_numeric: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    """Extract expression-backed/custom properties and Spreadsheet aliases deterministically."""
    parameters: list[dict[str, Any]] = []
    spreadsheets: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    excluded = {"Shape", "Placement", "Group", "Tip", "Origin", "Links", "LinkTransform"}
    for obj in sorted(getattr(document, "Objects", ()), key=lambda item: str(item.Name)):
        type_id = str(getattr(obj, "TypeId", ""))
        try:
            expression_map = {
                str(name): str(expression)
                for name, expression in (getattr(obj, "ExpressionEngine", ()) or ())
                if name and expression
            }
        except (AttributeError, RuntimeError, TypeError, ValueError, NotImplementedError):
            skipped.append({"object": str(obj.Name), "property": "ExpressionEngine"})
            expression_map = {}
        if type_id == "Spreadsheet::Sheet":
            cells = []
            try:
                cell_names = sorted(getattr(obj, "getNonEmptyCells", list)())
            except (AttributeError, RuntimeError, TypeError, ValueError, NotImplementedError):
                skipped.append({"object": str(obj.Name), "property": "cells"})
                cell_names = []
            for cell in cell_names:
                try:
                    alias = str(getattr(obj, "getAlias", lambda _cell: "")(cell) or "").strip()
                    contents = str(getattr(obj, "getContents", lambda _cell: "")(cell) or "")
                    computed = getattr(obj, "get", lambda _cell: None)(cell)
                    value, unit = to_numeric(computed)
                except (AttributeError, RuntimeError, TypeError, ValueError, NotImplementedError):
                    skipped.append({"object": str(obj.Name), "property": str(cell)})
                    continue
                cell_record = {
                    "cell": str(cell),
                    "alias": alias or None,
                    "contents": contents,
                    "value": value,
                    "unit": unit,
                }
                cells.append(cell_record)
                if alias:
                    parameters.append(
                        {
                            "name": alias,
                            "display_name": alias,
                            "value": value,
                            "unit": unit,
                            "expression": contents if contents.startswith("=") else None,
                            "is_key": True,
                            "is_user": True,
                            "feature_id": str(obj.Name),
                            "native_name": f"{obj.Name}.{cell}",
                        }
                    )
            spreadsheets.append({"name": str(obj.Name), "label": str(obj.Label), "cells": cells})
            continue
        if type_id == "Sketcher::SketchObject":
            normalized_expressions = {
                path.lstrip("."): expression for path, expression in expression_map.items()
            }
            for index, constraint in enumerate(getattr(obj, "Constraints", ()) or ()):
                label = str(getattr(constraint, "Name", "") or "").strip()
                paths = [f"Constraints[{index}]"]
                if label:
                    paths.append(f"Constraints.{label}")
                matches = {
                    normalized_expressions[path] for path in paths if path in normalized_expressions
                }
                if len(matches) > 1:
                    skipped.append({"object": str(obj.Name), "property": paths[0]})
                    continue
                native_name = paths[-1]
                try:
                    datum = obj.getDatum(index)
                    value, unit = to_numeric(datum)
                    if unit and unit.startswith("Unit:"):
                        rendered = re.fullmatch(
                            r"\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s*(.*?)\s*",
                            str(datum),
                        )
                        unit = rendered.group(1) or None if rendered else None
                except (AttributeError, RuntimeError, TypeError, ValueError, NotImplementedError):
                    value, unit = None, None
                if not label and not matches and value is None:
                    continue
                expression = next(iter(matches), None)
                is_driving = getattr(obj, "isDriving", None) or getattr(obj, "getDriving", None)
                try:
                    driving = bool(is_driving(index)) if callable(is_driving) else False
                except (AttributeError, RuntimeError, TypeError, ValueError, NotImplementedError):
                    driving = False
                parameters.append(
                    {
                        "name": f"{obj.Name}.{native_name}",
                        "display_name": label or native_name,
                        "value": value,
                        "unit": unit,
                        "expression": expression,
                        "is_key": expression is None and driving,
                        "is_user": False,
                        "feature_id": str(obj.Name),
                        "native_name": native_name,
                    }
                )
        for name in sorted(getattr(obj, "PropertiesList", ())):
            if name in excluded:
                continue
            expression = expression_map.get(name)
            try:
                group = str(getattr(obj, "getGroupOfProperty", lambda _name: "")(name) or "")
            except (AttributeError, RuntimeError, TypeError, ValueError, NotImplementedError):
                skipped.append({"object": str(obj.Name), "property": str(name)})
                group = ""
            is_user = bool(group and group not in {"Base", "Data", "Part Design"})
            if expression is None and not is_user:
                continue
            try:
                raw_value = getattr(obj, name)
                value, unit = to_numeric(raw_value)
            except (AttributeError, RuntimeError, TypeError, ValueError, NotImplementedError):
                skipped.append({"object": str(obj.Name), "property": str(name)})
                continue
            parameters.append(
                {
                    "name": f"{obj.Name}.{name}",
                    "display_name": name,
                    "value": value,
                    "unit": unit,
                    "expression": expression,
                    "is_key": bool(expression or is_user),
                    "is_user": is_user,
                    "feature_id": str(obj.Name),
                    "native_name": name,
                }
            )
    parameters.sort(key=lambda item: (str(item["name"]).casefold(), str(item["native_name"])))
    return parameters, spreadsheets, skipped


class ParameterExtractor:
    """Extract native parameters and conservatively resolve their dependencies."""

    def __init__(
        self,
        extract_native: Callable[
            [Any], tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]
        ],
    ):
        self.extract_native = extract_native

    def extract(self, document: Any) -> ProviderResult:
        started = time.monotonic()
        diagnostics: list[dict[str, Any]] = []
        try:
            parameters, spreadsheets, skipped = self.extract_native(document)
            if skipped:
                diagnostics.append(
                    {
                        "section": "parameters",
                        "code": "PARAMETER_PARTIAL_EXTRACTION",
                        "message": f"{len(skipped)} FreeCAD parameter properties or cells could not be extracted.",
                    }
                )
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            parameters, spreadsheets, skipped = [], [], []
            diagnostics.append(
                {
                    "section": "parameters",
                    "code": "PARAMETER_EXTRACTION_FAILED",
                    "message": f"Parameters and expressions could not be extracted: {error}",
                }
            )
        object_labels: dict[str, list[str]] = {}
        for obj in getattr(document, "Objects", ()):
            label = str(getattr(obj, "Label", "") or "")
            name = str(getattr(obj, "Name", "") or "")
            if label and name:
                object_labels.setdefault(label, []).append(name)
        graph = parameter_dependency_graph(parameters, object_labels=object_labels)
        if graph["cycles"]:
            diagnostics.append(
                {
                    "section": "parameters",
                    "code": "PARAMETER_DEPENDENCY_CYCLE",
                    "message": f"Detected {len(graph['cycles'])} cyclic parameter dependency paths.",
                }
            )
        if (
            graph["unresolved_references"]
            or graph["ambiguous_references"]
            or graph["unsupported_expressions"]
        ):
            diagnostics.append(
                {
                    "section": "parameters",
                    "code": "PARAMETER_DEPENDENCY_INCOMPLETE",
                    "message": "Some expression dependencies could not be resolved from extracted parameters.",
                }
            )
        return ProviderResult(
            records=parameters,
            diagnostics=diagnostics,
            evidence={"spreadsheets": spreadsheets, "graph": graph, "skipped": skipped},
            capability="partial" if diagnostics else "complete" if parameters else "unavailable",
            duration_ms=(time.monotonic() - started) * 1000,
        )
