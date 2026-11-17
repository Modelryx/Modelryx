"""Low-level FCStd and assembly inspection shared by preprocessing and extraction."""

from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

WORKBENCH_MODULES = {
    "ASSEMBLY3": ("freecad.asm3", "Asm3"),
    "ASSEMBLY4": ("freecad.asm4", "Asm4", "Asm4_libs"),
    "A2PLUS": ("a2p_importpart", "a2p_MuxAssembly"),
}

SOLVER_ASSEMBLY_MODELS = {"BUILTIN_ASSEMBLY", "ASSEMBLY3", "ASSEMBLY4", "A2PLUS"}


def wait_for_techdraw_tasks(document: Any, timeout_ms: int = 30_000) -> None:
    """Keep native drawing objects alive until their Qt background work finishes.

    FreeCAD 1.1.3 can schedule detail-view work even in headless mode. Closing
    the document first may destroy its base view while that work still uses it.
    The worker process is isolated, so waiting for its global Qt pool also covers
    tasks whose Python wrappers do not expose a per-view wait method.
    """
    if not any(
        str(getattr(obj, "TypeId", "")).startswith("TechDraw::")
        for obj in getattr(document, "Objects", ())
    ):
        return
    from PySide import QtCore

    if not QtCore.QThreadPool.globalInstance().waitForDone(timeout_ms):
        raise TimeoutError("Timed out waiting for native TechDraw tasks before document closure")


def close_document_safely(document: Any, freecad: Any) -> None:
    # Do not close on timeout: the outer worker deadline can terminate the
    # isolated process without freeing objects still in use by native threads.
    # A drawing can reference a base view/shape in another open document.
    # Drain its tasks before freeing that document as well.
    list_documents = getattr(freecad, "listDocuments", None)
    documents = list_documents().values() if callable(list_documents) else (document,)
    for opened in documents:
        wait_for_techdraw_tasks(opened)
    freecad.closeDocument(document.Name)


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def workbench_module_issue(
    model: str, available: Callable[[str], bool] = module_available
) -> dict[str, Any] | None:
    candidates = WORKBENCH_MODULES.get(model, ())
    if not candidates or any(available(name) for name in candidates):
        return None
    display_name = model.replace("ASSEMBLY", "Assembly")
    return {
        "code": "MISSING_FREECAD_WORKBENCH_MODULE",
        "severity": "ERROR",
        "assembly_model": model,
        "required_modules": list(candidates),
        "missing_modules": list(candidates),
        "message": f"{display_name} semantics were detected but its Python module is unavailable.",
        "suggestion": (
            f"Install and enable {display_name} in the FreeCAD worker image, then analyze the "
            "document again. Do not commit an assembly analyzed without its defining workbench."
        ),
    }


def _proxy_identity(obj: Any) -> str:
    proxy = getattr(obj, "Proxy", None)
    if proxy is None:
        return ""
    proxy_type = type(proxy)
    return f"{proxy_type.__module__}.{proxy_type.__name__}".casefold()


def assembly_model(document: Any) -> str:
    objects = list(getattr(document, "Objects", []))
    identities = " ".join(
        f"{getattr(obj, 'TypeId', '')} {_proxy_identity(obj)} "
        f"{getattr(obj, 'Name', '')} {getattr(obj, 'Label', '')}"
        for obj in objects
    ).casefold()
    if any(str(getattr(obj, "TypeId", "")).startswith("Assembly::") for obj in objects):
        return "BUILTIN_ASSEMBLY"
    if "assembly3" in identities or "asm3" in identities:
        return "ASSEMBLY3"
    if "assembly4" in identities or "asm4" in identities:
        return "ASSEMBLY4"
    if "a2plus" in identities or "a2p_" in identities:
        return "A2PLUS"
    if any(getattr(obj, "TypeId", "") in {"App::Link", "App::LinkGroup"} for obj in objects):
        return "APP_LINK"
    if any(getattr(obj, "TypeId", "") == "App::Part" for obj in objects):
        return "APP_PART"
    return "NONE"


def assembly_capability_statuses(
    model: str,
    *,
    is_assembly: bool,
    module_issue: dict[str, Any] | None,
    joint_count: int,
    skipped_joint_count: int,
    unverified_joint_suppression: int = 0,
    incomplete_joint_references: int = 0,
    unresolved_joint_occurrences: int = 0,
) -> dict[str, str]:
    """Keep structural, constraint, and solver resolution states distinct."""
    if not is_assembly:
        return {
            "assembly_semantics": "not_applicable",
            "assembly_constraints": "not_applicable",
            "assembly_solver": "not_applicable",
        }
    semantics = (
        "partial"
        if module_issue or skipped_joint_count or model in SOLVER_ASSEMBLY_MODELS
        else "complete"
    )
    constraints = (
        "partial"
        if module_issue or skipped_joint_count or model in {"ASSEMBLY3", "ASSEMBLY4", "A2PLUS"}
        else "complete"
        if joint_count
        else "not_applicable"
    )
    if unverified_joint_suppression or incomplete_joint_references or unresolved_joint_occurrences:
        semantics = "partial"
        constraints = "partial"
    if model not in SOLVER_ASSEMBLY_MODELS:
        solver = "not_applicable"
    else:
        # Extracting joint records does not inspect or execute the assembly solver.
        solver = "unavailable"
    return {
        "assembly_semantics": semantics,
        "assembly_constraints": constraints,
        "assembly_solver": solver,
    }


def fcstd_document_provenance(source: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(source) as archive:
            attributes = dict(ET.fromstring(archive.read("Document.xml")).attrib)
    except (KeyError, OSError, ET.ParseError, zipfile.BadZipFile):
        return {
            "format": "FCStd",
            "file_format_version": None,
            "document_schema_version": None,
            "authoring_freecad_version": None,
            "version_attributes": {},
        }
    folded = {key.casefold(): value for key, value in attributes.items()}
    return {
        "format": "FCStd",
        "file_format_version": folded.get("fileversion"),
        "document_schema_version": folded.get("schemaversion"),
        "authoring_freecad_version": folded.get("programversion"),
        "version_attributes": {
            key: value for key, value in attributes.items() if "version" in key.casefold()
        },
    }
