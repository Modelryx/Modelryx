"""An external worker must build and validate without the platform checkout."""

import ast
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_contract_snapshots_match_their_release_manifest():
    from modelryx_worker_sdk import CONTRACT_VERSION

    manifest = json.loads((ROOT / "contracts/snapshots.json").read_text(encoding="utf-8"))
    assert manifest["contractVersion"] == CONTRACT_VERSION
    for entry in manifest["files"]:
        assert hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    package = tomllib.loads((ROOT / "vendor/worker-sdk/pyproject.toml").read_text(encoding="utf-8"))
    assert package["project"]["version"] == manifest["sdkVersion"]
    schema = json.loads((ROOT / "contracts/part_metadata_v1.json").read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == manifest["partMetadataVersion"]


def test_worker_modules_have_no_platform_server_imports():
    for directory in ("converter", "preprocessor", "shared", "standalone", "tests"):
        for file in (ROOT / directory).rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                assert not any(n == "modelryx" or n.startswith("modelryx.") for n in names), file


def test_worker_imports_from_its_own_files_in_isolated_python(tmp_path):
    code = f"""
import sys
from pathlib import Path
root = Path({str(ROOT)!r})
sys.path[:0] = [str(root), str(root / 'converter'), str(root / 'shared'),
                str(root / 'vendor/worker-sdk/src')]
import modelryx_worker_sdk
import standalone.api
from metadata_contract import validator
assert Path(modelryx_worker_sdk.__file__).is_relative_to(root)
validator()
assert not any(n == 'modelryx' or n.startswith('modelryx.') for n in sys.modules)
"""
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_worker_metadata_contract_rejects_invalid_native_output():
    from jsonschema import ValidationError
    from metadata_contract import validate_part_metadata

    with pytest.raises(ValidationError):
        validate_part_metadata({"schema": "modelryx.part-metadata", "schema_version": "1.0"})


def test_contract_updates_require_an_explicit_refresh_without_a_platform_checkout(tmp_path):
    import shutil

    manifest = json.loads((ROOT / "contracts/snapshots.json").read_text(encoding="utf-8"))
    upstream = tmp_path / "upstream"
    for entry in manifest["files"]:
        source = upstream / entry["source"]
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / entry["path"], source)
    sdk = upstream / "worker-sdk/src/modelryx_worker_sdk/__init__.py"
    sdk.write_text(sdk.read_text(encoding="utf-8").replace(
        'CONTRACT_VERSION = "1.0"', 'CONTRACT_VERSION = "1.1"'), encoding="utf-8")
    target = tmp_path / "worker"
    (target / "scripts").mkdir(parents=True)
    script = target / "scripts/refresh-contracts.py"
    shutil.copyfile(ROOT / "scripts/refresh-contracts.py", script)
    result = subprocess.run([sys.executable, str(script), "--platform-root", str(upstream)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    refreshed = json.loads((target / "contracts/snapshots.json").read_text(encoding="utf-8"))
    assert refreshed["contractVersion"] == "1.1"
    assert json.loads((ROOT / "contracts/snapshots.json").read_text(encoding="utf-8")) == manifest
    for entry in refreshed["files"]:
        assert hashlib.sha256((target / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
