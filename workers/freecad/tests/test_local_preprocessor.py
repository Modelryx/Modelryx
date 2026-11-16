"""Exercise the generic preprocessor entry point without the native CAD runtime."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

spec = importlib.util.spec_from_file_location(
    "local_preprocessor", Path(__file__).parents[1] / "preprocessor/local_preprocessor.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("missing", [False, True])
def test_local_launcher_extracts_metadata_only_from_rebased_package(
    tmp_path, monkeypatch, capsys, missing
):
    source = tmp_path / "root.FCStd"
    source.write_bytes(b"original")
    output = tmp_path / "out"
    output.mkdir()
    package = output / "cad-package.zip"
    with ZipFile(package, "w") as archive:
        archive.writestr("root.FCStd", b"rebased")
    report = {
        "package": str(package),
        "root_logical_path": "root.FCStd",
        "missing_references": [{"reference": "child.FCStd"}] if missing else [],
        "isolated_reopen_validated": not missing,
    }
    calls = []

    def run(command, **options):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(stdout="native startup log\n" + json.dumps(report) + "\n")
        assert Path(command[3]).read_bytes() == b"rebased"
        assert "--metadata-only" in command
        assert options["stdout"] is module.sys.stderr
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main([str(source), str(output)]) == 0
    assert json.loads(capsys.readouterr().out) == report
    assert len(calls) == (1 if missing else 2)
    assert source.read_bytes() == b"original"
