from __future__ import annotations

import json
import sys
from pathlib import Path

CONVERTER = Path(__file__).resolve().parents[1] / "converter"
sys.path.insert(0, str(CONVERTER))

from dependency_evidence import dependency_report_evidence, find_dependency_report


def test_normalizes_resolved_and_missing_dependency_evidence() -> None:
    evidence = dependency_report_evidence(
        {
            "analyzer_version": "analyzer-1",
            "isolated_reopen_validated": False,
            "references": [
                {
                    "parent_logical_path": "Assembly.FCStd",
                    "child_logical_path": "parts/Bracket.FCStd",
                    "occurrence_id": "Bracket.LinkedObject",
                    "properties": {"source_object": "Bracket"},
                }
            ],
            "missing_references": [
                {
                    "code": "MISSING_FREECAD_REFERENCE",
                    "reference": "Missing.FCStd",
                    "referenced_by": "C:/private/Assembly.FCStd",
                    "object": "MissingLink",
                }
            ],
            "validation_issues": [],
        }
    )

    assert evidence["status"] == "partial"
    assert evidence["missing_count"] == 1
    assert {record["status"] for record in evidence["records"]} == {"resolved", "missing"}
    assert evidence["records"][1]["parent_document"] == "Assembly.FCStd"
    assert "C:/private" not in json.dumps(evidence)


def test_authoritative_empty_report_is_not_applicable() -> None:
    assert (
        dependency_report_evidence({"references": [], "missing_references": []})["status"]
        == "not_applicable"
    )


def test_finds_report_above_nested_root(tmp_path: Path) -> None:
    source = tmp_path / "assemblies" / "root.FCStd"
    source.parent.mkdir()
    source.write_bytes(b"fixture")
    report_path = tmp_path / "_modelryx" / "dependency-report.json"
    report_path.parent.mkdir()
    report = {
        "root_logical_path": "assemblies/root.FCStd",
        "references": [],
        "missing_references": [],
        "validation_issues": [],
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")

    assert find_dependency_report(source) == report


def test_unrelated_or_unsafe_report_root_is_not_used(tmp_path: Path) -> None:
    source = tmp_path / "root.FCStd"
    source.write_bytes(b"fixture")
    report_path = tmp_path / "_modelryx" / "dependency-report.json"
    report_path.parent.mkdir()
    report = {
        "root_logical_path": "other.FCStd",
        "references": [],
        "missing_references": [],
        "validation_issues": [],
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    assert find_dependency_report(source) is None
    report["root_logical_path"] = "../root.FCStd"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    assert find_dependency_report(source) is None


def test_malformed_report_does_not_claim_complete_evidence(tmp_path: Path) -> None:
    source = tmp_path / "root.FCStd"
    source.write_bytes(b"fixture")
    report_path = tmp_path / "_modelryx" / "dependency-report.json"
    report_path.parent.mkdir()
    report_path.write_text(
        json.dumps(
            {
                "root_logical_path": "root.FCStd",
                "references": "invalid",
                "missing_references": [],
                "validation_issues": [],
            }
        ),
        encoding="utf-8",
    )
    assert find_dependency_report(source) is None


def test_unvalidated_packaged_dependencies_are_partial() -> None:
    report = {
        "references": [{"parent_logical_path": "root.FCStd", "child_logical_path": "part.FCStd"}],
        "missing_references": [],
        "validation_issues": [],
        "isolated_reopen_validated": False,
    }
    assert dependency_report_evidence(report)["status"] == "partial"
    report["isolated_reopen_validated"] = True
    assert dependency_report_evidence(report)["status"] == "complete"


def test_missing_reference_hides_absolute_source_paths() -> None:
    report = {
        "references": [],
        "missing_references": [
            {"reference": "C:\\private\\Part.FCStd", "referenced_by": "C:\\private\\Root.FCStd"}
        ],
        "validation_issues": [],
    }
    evidence = dependency_report_evidence(report)
    assert evidence["records"][0]["document"] == "Part.FCStd"
    assert evidence["records"][0]["parent_document"] == "Root.FCStd"
