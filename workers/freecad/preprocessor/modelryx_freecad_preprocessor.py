"""Analyze and package a FreeCAD root document and its external FCStd links.

The module keeps graph and path policy independent from FreeCAD so it can be
unit-tested without a native installation.  ``main`` supplies the FreeCAD API.
"""

from __future__ import annotations

# FreeCAD rewrites the importing module's globals during initialization and can
# remove bindings created before its import. Load it before every other module.
try:
    import FreeCAD as freecad_runtime  # type: ignore
except ImportError:
    freecad_runtime = None

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

source_shared = Path(__file__).resolve().parents[1] / "shared"
if source_shared.is_dir():
    sys.path.insert(0, str(source_shared))

from modelryx_freecad_shared import (
    WORKBENCH_MODULES,
    assembly_model,
    close_document_safely,
    fcstd_document_provenance,
    module_available,
    workbench_module_issue,
)

FCSTD_SUFFIX = ".fcstd"
ANALYZER_VERSION = "modelryx-freecad-dependencies-4"
MAX_DEPENDENCY_DOCUMENTS = 500
MAX_DEPENDENCY_DEPTH = 64
MAX_DEPENDENCY_BYTES = 2 * 1024 * 1024 * 1024
MAX_ANALYSIS_SECONDS = 600.0
IDENTITY_TRANSFORM = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)

SUPPORTED_LINK_PROPERTY_TYPES = {
    "App::PropertyLink",
    "App::PropertyLinkChild",
    "App::PropertyLinkGlobal",
    "App::PropertyLinkList",
    "App::PropertyLinkSub",
    "App::PropertyLinkSubList",
    "App::PropertyXLink",
    "App::PropertyXLinkList",
    "App::PropertyXLinkSub",
    "App::PropertyXLinkSubList",
}

@dataclass(frozen=True)
class Reference:
    path: Path | None
    reference: str
    referenced_by: Path
    occurrence_name: str
    property_name: str = ""
    property_type: str = ""
    occurrence_id: str = ""
    transform: tuple[float, ...] = IDENTITY_TRANSFORM
    visible: bool = True
    suppressed: bool = False


def placement_transform(placement: Any) -> tuple[float, ...]:
    """Convert a FreeCAD placement to a row-major 4x4 matrix."""
    try:
        matrix = placement.toMatrix()
        return tuple(
            float(getattr(matrix, f"A{row}{column}"))
            for row in range(1, 5)
            for column in range(1, 5)
        )
    except Exception:  # noqa: BLE001 - optional native placement varies by object type
        return IDENTITY_TRANSFORM


def occurrence_transform(obj: Any) -> tuple[float, ...]:
    """Read a FreeCAD occurrence placement as a row-major 4x4 matrix."""
    return placement_transform(getattr(obj, "Placement", None))


def occurrence_visibility(obj: Any) -> bool:
    view = getattr(obj, "ViewObject", None)
    return bool(getattr(view, "Visibility", getattr(obj, "Visibility", True)))


def occurrence_suppressed(obj: Any) -> bool:
    for name in ("Suppressed", "IsSuppressed", "Suppression"):
        value = getattr(obj, name, None)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.casefold() in {"suppressed", "true", "yes"}:
            return True
    return False


def _objects(value: Any) -> Iterable[Any]:
    if value is None:
        return
    if hasattr(value, "Document"):
        yield value
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _objects(item)


# Recursively yield all FreeCAD objects from a given value, handling lists, tuples, and single objects.
def serialized_references(source: Path) -> list[Reference]:
    """Recover unresolved XLinks from the FCStd's authoritative Document.xml."""
    try:
        with zipfile.ZipFile(source) as archive:
            root = ET.fromstring(archive.read("Document.xml"))
    except (KeyError, ET.ParseError, zipfile.BadZipFile):
        return []
    result: list[Reference] = []
    for obj in root.iter("Object"):
        occurrence = obj.get("name", "")
        for prop in obj.iter("Property"):
            for index, link in enumerate(prop.iter("XLink")):
                raw = (link.get("file") or "").strip()
                if not raw or not raw.casefold().endswith(FCSTD_SUFFIX):
                    continue
                candidate = Path(raw)
                if not candidate.is_absolute():
                    candidate = source.parent / candidate
                result.append(
                    Reference(
                        candidate.resolve(),
                        raw,
                        source.resolve(),
                        occurrence,
                        prop.get("name", ""),
                        prop.get("type", ""),
                        (
                            f"{occurrence}.{prop.get('name', '')}[{index}]"
                            if index
                            else f"{occurrence}.{prop.get('name', '')}"
                        ).strip("."),
                    )
                )
    return result

# Extract the external references from a FreeCAD document, including those in link arrays and individual link properties.
def document_references(document: Any) -> list[Reference]:
    """Return external documents referenced through FreeCAD link properties."""
    source = Path(document.FileName).resolve()
    references: dict[tuple[str, str, str], Reference] = {}
    for obj in document.Objects:
        element_count = max(0, int(getattr(obj, "ElementCount", 0) or 0))
        element_list = list(getattr(obj, "ElementList", []) or [])
        is_link_array = getattr(obj, "TypeId", "") == "App::Link" and element_count > 0
        # Expanded arrays expose authoritative App::LinkElement objects in document.Objects.
        # The App::Link container points at the same target and must not become an extra occurrence.
        if is_link_array and element_list:
            continue
        for name in getattr(obj, "PropertiesList", []):
            try:
                property_type = str(obj.getTypeIdOfProperty(name))
            except Exception:  # noqa: BLE001 - FreeCAD proxies raise runtime-specific errors
                property_type = ""
            if "Link" not in property_type:
                continue
            try:
                value = getattr(obj, name)
            except Exception:  # noqa: BLE001,S112 - unreadable optional native property
                continue
            linked = list(_objects(value))
            for index, linked_object in enumerate(linked):
                linked_document = getattr(linked_object, "Document", None)
                filename = str(getattr(linked_document, "FileName", "") or "")
                if not filename:
                    continue
                path = Path(filename).resolve()
                if path == source:
                    continue
                occurrence = str(getattr(obj, "Label", "") or getattr(obj, "Name", "") or name)
                object_name = str(getattr(obj, "Name", "") or occurrence or name)
                if is_link_array and name == "LinkedObject" and len(linked) == 1:
                    placements = list(getattr(obj, "PlacementList", []) or [])
                    visibility = list(getattr(obj, "VisibilityList", []) or [])
                    for element_index in range(element_count):
                        element_name = f"{object_name}_i{element_index}"
                        occurrence_id = f"{element_name}.{name}"
                        transform = (
                            placement_transform(placements[element_index])
                            if element_index < len(placements)
                            else occurrence_transform(obj)
                        )
                        visible = (
                            bool(visibility[element_index])
                            if element_index < len(visibility)
                            else occurrence_visibility(obj)
                        )
                        references[(
                            os.path.normcase(str(path)), occurrence_id, name
                        )] = Reference(
                            path,
                            filename,
                            source,
                            f"{occurrence} [{element_index + 1}]",
                            name,
                            property_type,
                            occurrence_id,
                            transform,
                            visible,
                            occurrence_suppressed(obj),
                        )
                    continue
                occurrence_id = (
                    f"{object_name}.{name}[{index}]" if len(linked) > 1 else f"{object_name}.{name}"
                )
                references[(os.path.normcase(str(path)), occurrence_id, name)] = Reference(
                    path,
                    filename,
                    source,
                    occurrence,
                    name,
                    property_type,
                    occurrence_id,
                    occurrence_transform(obj),
                    occurrence_visibility(obj),
                    occurrence_suppressed(obj),
                )
            # XLink may retain a path-like textual value when its target is missing.
            if not linked and any(token in property_type for token in ("XLink", "File", "Path")):
                text = str(value or "")
                match = re.search(r"([^\n\r;]+\.FCStd)", text, re.IGNORECASE)
                if match:
                    raw = match.group(1).strip(" '\"[]()")
                    candidate = Path(raw)
                    if not candidate.is_absolute():
                        candidate = source.parent / candidate
                    candidate = candidate.resolve()
                    references.setdefault(
                        (os.path.normcase(str(candidate)), str(getattr(obj, "Name", name)), name),
                        Reference(
                            candidate,
                            raw,
                            source,
                            str(getattr(obj, "Label", "") or getattr(obj, "Name", "") or name),
                            name,
                            property_type,
                            f"{getattr(obj, 'Name', name)}.{name}",
                            occurrence_transform(obj),
                            occurrence_visibility(obj),
                            occurrence_suppressed(obj),
                        ),
                    )
    resolved_paths = {
        os.path.normcase(str(reference.path))
        for reference in references.values()
        if reference.path is not None
    }
    for reference in serialized_references(source):
        key = os.path.normcase(str(reference.path))
        if key not in resolved_paths:
            references[(key, reference.occurrence_id, "Document.xml")] = reference
    return sorted(
        references.values(),
        key=lambda item: (item.reference.casefold(), item.occurrence_id.casefold()),
    )


# Validate the properties of FreeCAD link objects, reporting any unsupported types.
def validate_reference_properties(document: Any, model: str) -> list[dict[str, Any]]:
    """Report populated workbench link properties the packager cannot safely interpret."""
    issues: list[dict[str, Any]] = []
    for obj in getattr(document, "Objects", []):
        for name in getattr(obj, "PropertiesList", []):
            try:
                property_type = str(obj.getTypeIdOfProperty(name))
                value = getattr(obj, name)
            except Exception:  # noqa: BLE001,S112 - optional native property
                continue
            if "Link" not in property_type or not value:
                continue
            if (
                property_type in SUPPORTED_LINK_PROPERTY_TYPES
                or property_type.startswith("App::Property")
            ):
                continue
            object_name = str(getattr(obj, "Label", "") or getattr(obj, "Name", "") or "<unknown>")
            issues.append(
                {
                    "code": "UNSUPPORTED_FREECAD_LINK_PROPERTY",
                    "severity": "ERROR",
                    "assembly_model": model,
                    "object": object_name,
                    "property": name,
                    "property_type": property_type,
                    "message": (
                        f"{object_name}.{name} uses unsupported FreeCAD link type {property_type}."
                    ),
                    "suggestion": (
                        "Replace this reference with a standard App::Link/XLink, or install a "
                        "Modelryx worker version that supports this assembly workbench before committing."
                    ),
                }
            )
    return issues


# Classify the purpose of a FreeCAD document based on its contents.
def classify_document(document: Any) -> str:
    objects = list(document.Objects)
    # Drawings commonly link to a model in another document.  Detect the
    # authored document purpose before treating any external reference as an
    # assembly occurrence.
    if any(
        str(getattr(obj, "TypeId", "")).startswith("TechDraw::")
        for obj in objects
    ):
        return "DRAWING"
    assembly_types = {
        "App::Part",
        "App::LinkGroup",
        "Assembly::AssemblyObject",
        "Assembly::Assembly",
    }
    external = any(reference.path for reference in document_references(document))
    if external or any(
        getattr(obj, "TypeId", "") in assembly_types
        or getattr(obj, "TypeId", "") == "App::Link"
        for obj in objects
    ):
        return "ASSEMBLY"
    if any(getattr(obj, "Shape", None) is not None for obj in objects):
        return "PART"
    if any(
        str(getattr(obj, "TypeId", "")).startswith("Mesh::")
        and getattr(obj, "Mesh", None) is not None
        for obj in objects
    ):
        return "PART"
    return "UNKNOWN"


# Detect cycles in the FreeCAD document dependency graph.
def dependency_cycles(
    edges: Iterable[tuple[Path, Path, str, str, tuple[float, ...], bool, bool]],
) -> list[list[Path]]:
    """Return each directed document cycle once in deterministic order."""
    adjacency: dict[Path, set[Path]] = {}
    for parent, child, *_occurrence_data in edges:
        adjacency.setdefault(parent.resolve(), set()).add(child.resolve())
        adjacency.setdefault(child.resolve(), set())
    state: dict[Path, int] = {}
    stack: list[Path] = []
    stack_positions: dict[Path, int] = {}
    cycles: dict[tuple[str, ...], list[Path]] = {}

    def record(nodes: list[Path]) -> None:
        ring = nodes[:-1]
        if not ring:
            return
        keys = [os.path.normcase(str(node)) for node in ring]
        rotations = [tuple(keys[index:] + keys[:index]) for index in range(len(keys))]
        canonical = min(rotations)
        start = rotations.index(canonical)
        ordered = ring[start:] + ring[:start]
        cycles[canonical] = [*ordered, ordered[0]]

    def visit(node: Path) -> None:
        state[node] = 1
        stack_positions[node] = len(stack)
        stack.append(node)
        for child in sorted(adjacency[node], key=lambda value: str(value).casefold()):
            if state.get(child, 0) == 0:
                visit(child)
            elif state.get(child) == 1:
                record([*stack[stack_positions[child] :], child])
        stack.pop()
        stack_positions.pop(node, None)
        state[node] = 2

    for node in sorted(adjacency, key=lambda value: str(value).casefold()):
        if state.get(node, 0) == 0:
            visit(node)
    return [cycles[key] for key in sorted(cycles)]


# Compute the logical paths for the FreeCAD documents, ensuring uniqueness and safety.
def _reserved_external_numbers(root_directory: Path) -> set[int]:
    result: set[int] = set()
    for child in root_directory.iterdir():
        if not child.is_dir():
            continue
        match = re.fullmatch(r"external(\d+)", child.name, re.IGNORECASE)
        if match:
            result.add(int(match.group(1)))
    return result

# Compute the set of reserved external numbers in the FreeCAD workspace.
def logical_paths(root: Path, files: Iterable[Path]) -> dict[Path, str]:
    root = root.resolve()
    root_directory = root.parent
    resolved = sorted({path.resolve() for path in files}, key=lambda path: str(path).casefold())
    result = {root: root.name}
    external_roots: dict[Path, int] = {}
    reserved = _reserved_external_numbers(root_directory)
    next_number = 1
    for path in resolved:
        if path == root:
            continue
        try:
            relative = path.relative_to(root_directory)
            logical = PurePosixPath(*relative.parts).as_posix()
        except ValueError:
            external_root = path.parent
            if external_root not in external_roots:
                while next_number in reserved:
                    next_number += 1
                external_roots[external_root] = next_number
                reserved.add(next_number)
                next_number += 1
            logical = f"external{external_roots[external_root]}/{path.name}"
        if PurePosixPath(logical).is_absolute() or ".." in PurePosixPath(logical).parts:
            raise ValueError(f"Unsafe FreeCAD dependency path: {logical}")
        result[path] = logical
    folded: dict[str, Path] = {}
    for path, logical in result.items():
        key = logical.casefold()
        if key in folded and folded[key] != path:
            raise ValueError(f"FreeCAD dependency paths collide after normalization: {logical}")
        folded[key] = path
    return result


def occurrence_key(parent_logical_path: str, occurrence_id: str) -> str:
    """Return a stable BOM key that fits the persisted 255-character contract."""
    value = f"{parent_logical_path}::{occurrence_id}"
    if len(value) <= 255:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{value[:236]}::{digest}"

# Analyze the FreeCAD document dependency graph, collecting information about documents, assemblies,
# and validation issues.
def analyze(
    root: Path,
    open_document: Callable[[Path], Any],
    close_document: Callable[[Any], None],
    *,
    max_documents: int = MAX_DEPENDENCY_DOCUMENTS,
    max_depth: int = MAX_DEPENDENCY_DEPTH,
    max_total_bytes: int = MAX_DEPENDENCY_BYTES,
    max_seconds: float = MAX_ANALYSIS_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    is_module_available: Callable[[str], bool] = module_available,
) -> tuple[
    str,
    list[Path],
    list[dict[str, Any]],
    list[tuple[Path, Path, str, str, tuple[float, ...], bool, bool]],
    dict[Path, str],
    dict[Path, str],
    list[dict[str, str]],
]:
    root = root.resolve()
    pending = [(root, 0)]
    visited: dict[str, Path] = {}
    missing: dict[tuple[str, str], dict[str, str]] = {}
    edges: set[tuple[Path, Path, str, str, tuple[float, ...], bool, bool]] = set()
    document_types: dict[Path, str] = {}
    assembly_models: dict[Path, str] = {}
    validation_issues: list[dict[str, Any]] = []
    document_type = "UNKNOWN"
    total_bytes = 0
    started_at = clock()
    while pending:
        source, depth = pending.pop()
        key = os.path.normcase(str(source))
        if key in visited:
            continue
        if clock() - started_at > max_seconds:
            validation_issues.append(
                {
                    "document": str(source),
                    "code": "FREECAD_ANALYSIS_TIME_LIMIT",
                    "severity": "ERROR",
                    "message": f"FreeCAD dependency analysis exceeded {max_seconds:g} seconds.",
                    "suggestion": (
                        "Reduce the assembly size or reference depth, verify that every document "
                        "opens promptly in FreeCAD, and analyze the root document again."
                    ),
                }
            )
            break
        if depth > max_depth:
            validation_issues.append(
                {
                    "document": str(source),
                    "code": "FREECAD_DEPENDENCY_DEPTH_LIMIT",
                    "severity": "ERROR",
                    "message": (
                        f"FreeCAD dependency depth exceeds the configured limit of {max_depth} "
                        f"at {source.name}."
                    ),
                    "suggestion": (
                        "Reduce nested external-document references or split the assembly into "
                        "independently managed subassemblies, then analyze it again."
                    ),
                }
            )
            continue
        if len(visited) >= max_documents:
            validation_issues.append(
                {
                    "document": str(source),
                    "code": "FREECAD_DEPENDENCY_COUNT_LIMIT",
                    "severity": "ERROR",
                    "message": (
                        "FreeCAD dependency analysis exceeds the configured limit of "
                        f"{max_documents} documents."
                    ),
                    "suggestion": (
                        "Split the assembly into smaller managed subassemblies or ask an "
                        "administrator to review the dependency limit."
                    ),
                }
            )
            break
        if not source.is_file():
            missing[(str(source), str(source))] = {
                "reference": str(source),
                "referenced_by": str(source),
                "suggestion": "Place or relink the referenced FCStd file where FreeCAD expects it.",
            }
            continue
        try:
            source_bytes = source.stat().st_size
        except OSError:
            source_bytes = 0
        if total_bytes + source_bytes > max_total_bytes:
            validation_issues.append(
                {
                    "document": str(source),
                    "code": "FREECAD_DEPENDENCY_SIZE_LIMIT",
                    "severity": "ERROR",
                    "message": (
                        "FreeCAD dependency sources exceed the configured total-size limit of "
                        f"{max_total_bytes} bytes."
                    ),
                    "suggestion": (
                        "Reduce the source set, remove unnecessary embedded data, or ask an "
                        "administrator to review the dependency-size limit."
                    ),
                }
            )
            break
        total_bytes += source_bytes
        try:
            document = open_document(source)
        except Exception:  # noqa: BLE001 - native FreeCAD failures vary by release/workbench
            visited[key] = source
            validation_issues.append(
                {
                    "document": str(source),
                    "code": "UNREADABLE_FREECAD_DOCUMENT",
                    "severity": "ERROR",
                    "message": f"FreeCAD could not open dependency document {source.name}.",
                    "suggestion": (
                        "Open and repair or resave the document in a supported FreeCAD version, "
                        "then analyze the root document again."
                    ),
                }
            )
            continue
        try:
            visited[key] = source
            document_types[source] = classify_document(document)
            assembly_models[source] = assembly_model(document)
            module_issue = workbench_module_issue(
                assembly_models[source], is_module_available
            )
            if module_issue is not None:
                validation_issues.append({**module_issue, "document": str(source)})
            for issue in validate_reference_properties(document, assembly_models[source]):
                validation_issues.append({**issue, "document": str(source)})
            if source == root:
                document_type = document_types[source]
            for reference in document_references(document):
                if reference.path is None or not reference.path.is_file():
                    missing[(reference.reference, str(source))] = {
                        "code": "MISSING_FREECAD_REFERENCE",
                        "reference": reference.reference,
                        "referenced_by": str(source),
                        "assembly_model": assembly_models[source],
                        "object": reference.occurrence_name,
                        "property": reference.property_name,
                        "suggestion": "Place or relink the referenced FCStd file where FreeCAD expects it.",
                    }
                else:
                    edges.add(
                        (
                            source,
                            reference.path,
                            reference.occurrence_name,
                            reference.occurrence_id,
                            reference.transform,
                            reference.visible,
                            reference.suppressed,
                        )
                    )
                    pending.append((reference.path, depth + 1))
        finally:
            close_document(document)
    for cycle in dependency_cycles(edges):
        cycle_text = " -> ".join(path.name for path in cycle)
        validation_issues.append(
            {
                "document": str(cycle[0]),
                "code": "CYCLIC_FREECAD_REFERENCE",
                "severity": "ERROR",
                "message": f"FreeCAD document references form a cycle: {cycle_text}.",
                "cycle": [str(path) for path in cycle],
                "suggestion": (
                    "Remove or replace one external link in the reported cycle, then analyze the "
                    "root document again. Modelryx cannot create an isolated package from cyclic "
                    "document references."
                ),
            }
        )
    return (
        document_type,
        list(visited.values()),
        list(missing.values()),
        sorted(
            edges,
            key=lambda edge: (
                str(edge[0]).casefold(),
                str(edge[1]).casefold(),
                edge[2].casefold(),
                edge[3].casefold(),
            ),
        ),
        document_types,
        assembly_models,
        validation_issues,
    )

# Build a CAD package from the analyzed FreeCAD documents and their references.
# This function takes the analyzed FreeCAD documents and their references and
# builds a CAD package in the specified output directory.
def build_package(
    root: Path,
    output_directory: Path,
    document_type: str,
    files: list[Path],
    missing: list[dict[str, str]],
    references: list[
        tuple[Path, Path, str, str, tuple[float, ...], bool, bool]
    ] | None = None,
    document_types: dict[Path, str] | None = None,
    assembly_models: dict[Path, str] | None = None,
    validation_issues: list[dict[str, Any]] | None = None,
    packaged_sources: dict[Path, Path] | None = None,
    isolated_reopen_validated: bool = False,
) -> dict[str, Any]:
    paths = logical_paths(root, files)
    output_directory.mkdir(parents=True, exist_ok=True)
    package = output_directory / "cad-package.zip"
    report = {
        "contract_version": "1.0",
        "analyzer_version": ANALYZER_VERSION,
        "document_type": document_type,
        "root_file": root.name,
        "root_logical_path": root.name,
        "workbench_requirements": [
            {
                "assembly_model": model,
                "required_modules": list(WORKBENCH_MODULES.get(model, ())),
                "missing_modules": sorted(
                    {
                        module
                        for issue in (validation_issues or [])
                        if issue.get("assembly_model") == model
                        for module in issue.get("missing_modules", [])
                    }
                ),
            }
            for model in sorted(set((assembly_models or {}).values()))
            if model in WORKBENCH_MODULES
        ],
        "files": [
            {
                "source_path": str(path),
                "logical_path": logical,
                "is_root": path == root,
                "document_type": (document_types or {}).get(path, "UNKNOWN"),
                "assembly_model": (assembly_models or {}).get(path, "NONE"),
            }
            for path, logical in sorted(paths.items(), key=lambda item: item[1].casefold())
        ],
        "references": [
            {
                "parent_logical_path": paths[parent],
                "child_logical_path": paths[child],
                "instance_name": occurrence,
                "occurrence_id": occurrence_id,
                "occurrence_path": f"{paths[parent]}::{occurrence_id}",
                "occurrence_key": occurrence_key(paths[parent], occurrence_id),
                "transform": list(transform),
                "is_visible": visible,
                "is_suppressed": suppressed,
                "source_reference": occurrence_id,
                "reference_designator": occurrence,
                "properties": {
                    "source_document": paths[parent],
                    "source_object": occurrence_id.split(".", 1)[0],
                    "assembly_model": (assembly_models or {}).get(parent, "NONE"),
                },
            }
            for (
                parent,
                child,
                occurrence,
                occurrence_id,
                transform,
                visible,
                suppressed,
            ) in (references or [])
            if parent in paths and child in paths
        ],
        "missing_references": missing,
        "validation_issues": [
            {
                **issue,
                "document": paths.get(
                    Path(issue["document"]).resolve(), Path(issue["document"]).name
                ),
                **(
                    {
                        "cycle": [
                            paths.get(Path(value).resolve(), Path(value).name)
                            for value in issue["cycle"]
                        ]
                    }
                    if issue.get("cycle")
                    else {}
                ),
            }
            for issue in (validation_issues or [])
        ],
        "isolated_reopen_validated": isolated_reopen_validated,
    }
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, logical in paths.items():
            archive.write((packaged_sources or {}).get(path, path), logical)
        archive.writestr("_modelryx/dependency-report.json", json.dumps(report, indent=2))
    return {**report, "package": str(package)}

# Replace XLink targets in the serialized FreeCAD document with their packaged counterparts.
def _replace_link_targets(
    value: Any,
    source_to_packaged: dict[Path, Path],
    packaged_documents: dict[Path, Any],
    open_document: Callable[[Path], Any],
) -> tuple[Any, bool]:
    if hasattr(value, "Document"):
        source_name = str(getattr(value.Document, "FileName", "") or "")
        if not source_name:
            return value, False
        packaged_path = source_to_packaged.get(Path(source_name).resolve())
        if packaged_path is None:
            return value, False
        target_document = packaged_documents.get(packaged_path)
        if target_document is None:
            target_document = open_document(packaged_path)
            packaged_documents[packaged_path] = target_document
        target = target_document.getObject(value.Name)
        if target is None:
            raise ValueError(
                f"Packaged FreeCAD dependency {packaged_path.name} has no object {value.Name}."
            )
        return target, target is not value
    if isinstance(value, tuple):
        replaced = [
            _replace_link_targets(item, source_to_packaged, packaged_documents, open_document)
            for item in value
        ]
        return tuple(item for item, _changed in replaced), any(
            changed for _item, changed in replaced
        )
    if isinstance(value, list):
        replaced = [
            _replace_link_targets(item, source_to_packaged, packaged_documents, open_document)
            for item in value
        ]
        return [item for item, _changed in replaced], any(changed for _item, changed in replaced)
    return value, False

# Rewrite the XLink paths in a FreeCAD document to point to their packaged copies.
def _rewrite_serialized_xlinks(
    source: Path,
    packaged: Path,
    source_to_packaged: dict[Path, Path],
) -> None:
    """Point authoritative FCStd XLink paths at their packaged copies."""
    try:
        with zipfile.ZipFile(packaged, "r") as archive:
            document_xml = archive.read("Document.xml")
            entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
        root = ET.fromstring(document_xml)
    except (KeyError, ET.ParseError, zipfile.BadZipFile):
        return
    changed = False
    for link in root.iter("XLink"):
        raw = (link.get("file") or "").strip()
        if not raw:
            continue
        referenced = Path(raw)
        if not referenced.is_absolute():
            referenced = source.parent / referenced
        target = source_to_packaged.get(referenced.resolve())
        if target is None:
            continue
        relative = os.path.relpath(target, packaged.parent).replace("\\", "/")
        link.set("file", relative)
        changed = True
    if not changed:
        return
    rewritten_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    temporary = packaged.with_name(f".{packaged.name}.modelryx-rewrite")
    with zipfile.ZipFile(temporary, "w") as archive:
        for info, content in entries:
            archive.writestr(info, rewritten_xml if info.filename == "Document.xml" else content)
    try:
        temporary.replace(packaged)
    except PermissionError:
        # Some Windows-managed temporary directories deny ReplaceFile-style
        # renames even though both files are writable. Preserve the same
        # result with an overwrite followed by cleanup.
        shutil.copyfile(temporary, packaged)
        temporary.unlink()

# Rebase the FreeCAD documents to a new workspace, copying them and updating their XLink references.
def rebase_package_copies(
    root: Path,
    paths: dict[Path, str],
    workspace: Path,
    open_document: Callable[[Path], Any],
    close_document: Callable[[Any], None],
) -> dict[Path, Path]:
    """Copy, rebind, and isolated-reopen a native FreeCAD document graph."""
    source_to_packaged = {
        source: workspace.joinpath(*PurePosixPath(logical).parts)
        for source, logical in paths.items()
    }
    for source, packaged in source_to_packaged.items():
        packaged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, packaged)
    for source, packaged in source_to_packaged.items():
        _rewrite_serialized_xlinks(source, packaged, source_to_packaged)

    packaged_documents: dict[Path, Any] = {}
    try:
        for source, packaged in source_to_packaged.items():
            document = packaged_documents.get(packaged)
            if document is None:
                document = open_document(packaged)
                packaged_documents[packaged] = document
            changed = False
            for obj in document.Objects:
                for name in getattr(obj, "PropertiesList", []):
                    try:
                        property_type = str(obj.getTypeIdOfProperty(name))
                        value = getattr(obj, name)
                    except Exception:  # noqa: BLE001,S112 - optional native property
                        continue
                    if "Link" not in property_type:
                        continue
                    replacement, property_changed = _replace_link_targets(
                        value,
                        source_to_packaged,
                        packaged_documents,
                        open_document,
                    )
                    if property_changed:
                        try:
                            setattr(obj, name, replacement)
                        except Exception as exc:
                            raise ValueError(
                                f"Could not rebind {source.name}:{obj.Name}.{name} to its packaged dependency."
                            ) from exc
                        changed = True
            if changed:
                document.recompute()
                document.save()
    finally:
        for document in list(packaged_documents.values()):
            close_document(document)

    packaged_root = source_to_packaged[root]
    allowed = {path.resolve() for path in source_to_packaged.values()}
    validation_documents: dict[Path, Any] = {}
    pending = [packaged_root]
    try:
        while pending:
            path = pending.pop().resolve()
            if path in validation_documents:
                continue
            document = open_document(path)
            validation_documents[path] = document
            for reference in document_references(document):
                if reference.path is None or reference.path.resolve() not in allowed:
                    raise ValueError(
                        f"Packaged FreeCAD reference {reference.reference} from {path.name} "
                        "still resolves outside the isolated package. Relink the source assembly "
                        "with relative references and try again."
                    )
                pending.append(reference.path)
    finally:
        for document in list(validation_documents.values()):
            close_document(document)
    return source_to_packaged


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze and package one FreeCAD root document")
    parser.add_argument("input", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    if not args.input.is_file() or args.input.suffix.casefold() != FCSTD_SUFFIX:
        raise ValueError("Input must be an existing .FCStd document.")
    if freecad_runtime is None:
        raise RuntimeError("FreeCAD dependency analysis requires the FreeCAD Python runtime.")

    opened: dict[str, Any] = {}

    def open_document(path: Path) -> Any:
        document = freecad_runtime.open(str(path))
        document.recompute()
        opened[document.Name] = document
        return document

    def close_document(document: Any) -> None:
        opened.pop(document.Name, None)
        close_document_safely(document, freecad_runtime)

    (
        document_type,
        files,
        missing,
        references,
        document_types,
        assembly_models,
        validation_issues,
    ) = analyze(
        args.input, open_document, close_document
    )
    root = args.input.resolve()
    paths = logical_paths(root, files)
    packaged_sources = None
    isolated_reopen_validated = False
    if not missing and not validation_issues:
        with tempfile.TemporaryDirectory(prefix="modelryx-freecad-package-") as directory:
            packaged_sources = rebase_package_copies(
                root, paths, Path(directory), open_document, close_document
            )
            report = build_package(
                root,
                args.output_directory.resolve(),
                document_type,
                files,
                missing,
                references,
                document_types,
                assembly_models,
                validation_issues,
                packaged_sources,
                isolated_reopen_validated=True,
            )
            isolated_reopen_validated = True
    if not isolated_reopen_validated:
        report = build_package(
            root,
            args.output_directory.resolve(),
            document_type,
            files,
            missing,
            references,
            document_types,
            assembly_models,
            validation_issues,
        )
    print(json.dumps(report, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
