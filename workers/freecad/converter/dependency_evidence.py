"""Normalize authoritative dependency-preprocessor evidence for metadata extraction."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

MAX_REPORT_BYTES = 16 * 1024 * 1024


def _logical_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {".", ".."} for part in normalized.split("/"))
        or any(not part for part in normalized.split("/"))
    ):
        return None
    return path.as_posix()


def _valid_report(report: Any) -> bool:
    if not isinstance(report, dict) or _logical_path(report.get("root_logical_path")) is None:
        return False
    for field in ("references", "missing_references", "validation_issues"):
        if not isinstance(report.get(field), list) or not all(
            isinstance(item, dict) for item in report[field]
        ):
            return False
    for item in report["references"]:
        if (
            _logical_path(item.get("parent_logical_path")) is None
            or _logical_path(item.get("child_logical_path")) is None
            or not isinstance(item.get("properties", {}), dict)
        ):
            return False
    return True


def find_dependency_report(source: Path, max_parents: int = 16) -> dict[str, Any] | None:
    """Find an embedded package report above a packaged root document."""
    source = source.resolve()
    directory = source.parent
    for _index in range(max_parents):
        candidate = directory / "_modelryx" / "dependency-report.json"
        if candidate.is_file():
            try:
                if candidate.stat().st_size > MAX_REPORT_BYTES:
                    return None
                value = json.loads(candidate.read_text(encoding="utf-8"))
                if not _valid_report(value):
                    return None
                root_path = _logical_path(value["root_logical_path"])
                if root_path is not None and (directory / root_path).resolve() == source:
                    return value
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                return None
        if directory.parent == directory:
            break
        directory = directory.parent
    return None


def dependency_report_evidence(report: dict[str, Any] | None) -> dict[str, Any]:
    """Return path-safe dependency records and an evidence-based status."""
    if not report:
        return {"available": False, "status": "unavailable", "records": []}
    records: list[dict[str, Any]] = []
    for reference in report.get("references", []):
        if not isinstance(reference, dict) or not reference.get("child_logical_path"):
            continue
        records.append(
            {
                "document": str(reference["child_logical_path"]),
                "parent_document": str(reference.get("parent_logical_path") or ""),
                "object": str(reference.get("properties", {}).get("source_object") or ""),
                "occurrence_id": str(reference.get("occurrence_id") or ""),
                "external": True,
                "status": "resolved",
                "source": "dependency_preprocessor",
            }
        )
    for missing in report.get("missing_references", []):
        if not isinstance(missing, dict):
            continue
        raw_reference = str(missing.get("reference") or "")
        reference = (
            _logical_path(raw_reference) or raw_reference.replace("\\", "/").rsplit("/", 1)[-1]
        )
        parent = str(missing.get("referenced_by") or "").replace("\\", "/").rsplit("/", 1)[-1]
        records.append(
            {
                "document": reference,
                "parent_document": parent,
                "object": str(missing.get("object") or ""),
                "external": True,
                "status": "missing",
                "code": str(missing.get("code") or "MISSING_FREECAD_REFERENCE"),
                "source": "dependency_preprocessor",
            }
        )
    missing_count = sum(record["status"] == "missing" for record in records)
    issue_count = len(report.get("validation_issues", []))
    dependency_count = len(records)
    isolated_reopen_validated = report.get("isolated_reopen_validated") is True
    return {
        "available": True,
        "status": (
            "partial"
            if missing_count or issue_count or (dependency_count and not isolated_reopen_validated)
            else "complete"
            if dependency_count
            else "not_applicable"
        ),
        "records": sorted(
            records,
            key=lambda item: (
                str(item.get("parent_document", "")).casefold(),
                str(item.get("document", "")).casefold(),
                str(item.get("occurrence_id", "")).casefold(),
            ),
        ),
        "missing_count": missing_count,
        "validation_issue_count": issue_count,
        "isolated_reopen_validated": isolated_reopen_validated,
        "analyzer_version": str(report.get("analyzer_version") or ""),
    }
