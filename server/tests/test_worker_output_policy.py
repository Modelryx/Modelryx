import copy
import json
import stat
import zipfile
from types import SimpleNamespace

import pytest

from modelryx.services import external_workers
from modelryx.services.worker_output_bundles import validate_worker_bundle
from modelryx.services.worker_output_policy import allowed_worker_outputs, preferred_worker_outputs


def capabilities(system="Example CAD"):
    return {
        "contractVersion": "1.0", "cadSystem": system,
        "documentKinds": ["part", "assembly", "drawing"],
        "sourceExtensions": [".part", ".assembly", ".drawing"],
        "outputRoles": ["native-snapshot", "part-metadata", "model-preview", "glb", "drawing-pdf", "component-geometry"],
        "processingProfile": {
            "preferredOutputs": {"part": ["glb"], "assembly": ["glb", "component-geometry"], "drawing": ["drawing-pdf"]},
            "outputContracts": {"component-geometry": "component-geometry-step-v1"},
            "includeRelatedDrawings": True,
        },
    }


@pytest.mark.parametrize("worker_id", ["solidworks", "engineering-west", "cad-17"])
@pytest.mark.parametrize("system", ["solidworks", "Example CAD", "Other Vendor"])
def test_registration_id_and_cad_system_do_not_select_output_policy(monkeypatch, worker_id, system):
    caps = external_workers.validate_capabilities(capabilities(system))
    worker = {"id": worker_id, "enabled": True, "capabilities": caps}
    monkeypatch.setattr(external_workers, "discover_workers", lambda *_: [worker])
    matched = external_workers.matching_worker(object(), "root.assembly", system, object())
    assert matched["id"] == worker_id
    profile = matched["capabilities"]["processingProfile"]
    assert preferred_worker_outputs("assembly", True, caps["outputRoles"], profile) == ["glb", "component-geometry"]
    assert preferred_worker_outputs("drawing", False, caps["outputRoles"], profile) == ["drawing-pdf"]


@pytest.mark.parametrize("change", [
    lambda p: p.update(preferredOutputs=[]),
    lambda p: p["preferredOutputs"].pop("drawing"),
    lambda p: p["preferredOutputs"].update(unknown=[]),
    lambda p: p["preferredOutputs"].update(part=["glb", "glb"]),
    lambda p: p["preferredOutputs"].update(part=["not-advertised"]),
    lambda p: p["preferredOutputs"].update(part=["native-snapshot"]),
    lambda p: p["outputContracts"].update({"component-geometry": "unknown-v99"}),
    lambda p: p["outputContracts"].update({"unknown": None}),
    lambda p: p.update(includeRelatedDrawings="true"),
    lambda p: p.update(includeRelatedDrawings=1),
])
def test_invalid_profiles_are_rejected(change):
    caps = capabilities()
    change(caps["processingProfile"])
    with pytest.raises(ValueError):
        external_workers.validate_capabilities(caps)


def test_legacy_workers_keep_their_existing_profile():
    caps = capabilities()
    del caps["processingProfile"]
    validated = external_workers.validate_capabilities(caps)
    assert "processingProfile" not in validated
    assert preferred_worker_outputs("assembly", True, caps["outputRoles"]) == ["glb"]


def test_bundle_publication_requires_an_exact_supported_contract():
    settings = SimpleNamespace(max_preview_bom_bytes=10, max_preview_glb_bytes=20,
                               max_pdf_preview_bytes=30, max_preview_package_bytes=40)
    profile = capabilities()["processingProfile"]
    assert "component-geometry" not in allowed_worker_outputs(settings)
    assert allowed_worker_outputs(settings, profile)["component-geometry"] == ("application/zip", 40)
    assert "unknown" not in allowed_worker_outputs(settings, {"outputContracts": {"unknown": None}})
    bad = copy.deepcopy(profile)
    bad["outputContracts"]["component-geometry"] = "unknown-v99"
    assert "component-geometry" not in allowed_worker_outputs(settings, bad)


def bundle(tmp_path, *, mutate=None, payload=b"ISO-10303-21;\nEND-ISO-10303-21;", extra=None):
    manifest = {"geometryUnits": "millimeter", "components": [{
        "file": "part.step", "occurrenceId": "occ-1", "partId": "definition-1", "nodeType": "part",
        "transformUnits": "millimeter", "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    }]}
    if mutate:
        mutate(manifest)
    path = tmp_path / "components.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("part.step", payload)
        if extra:
            archive.writestr(*extra)
    return path


def test_generic_bundle_validation_preserves_content_guards(tmp_path):
    path = bundle(tmp_path)
    result = validate_worker_bundle(path, "component-geometry", 100000, 100000,
                                    contract="component-geometry-step-v1")
    assert result["components"][0]["occurrenceId"] == "occ-1"
    with pytest.raises(RuntimeError, match="contract"):
        validate_worker_bundle(path, "component-geometry", 100000, 100000, contract="unknown-v99")
    with pytest.raises(RuntimeError, match="size"):
        validate_worker_bundle(path, "component-geometry", 1, 100000, contract="component-geometry-step-v1")


@pytest.mark.parametrize("case", ["truncated", "duplicate", "transform", "traversal", "undeclared", "symlink"])
def test_generic_component_bundle_rejects_unsafe_content(tmp_path, case):
    kwargs = {}
    if case == "truncated":
        kwargs["payload"] = b"ISO-10303-21; truncated"
    elif case == "duplicate":
        kwargs["mutate"] = lambda m: m["components"].append(copy.deepcopy(m["components"][0]))
    elif case == "transform":
        kwargs["mutate"] = lambda m: m["components"][0]["transform"].__setitem__(15, 0)
    elif case in {"traversal", "undeclared"}:
        kwargs["extra"] = ("../escape" if case == "traversal" else "undeclared.step", b"data")
    else:
        link = zipfile.ZipInfo("link.step")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        kwargs["extra"] = (link, b"part.step")
    with pytest.raises(RuntimeError):
        validate_worker_bundle(bundle(tmp_path, **kwargs), "component-geometry", 100000, 100000,
                               contract="component-geometry-step-v1")
