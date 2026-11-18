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
def test_local_launcher_only_packages_dependencies(
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
        assert Path(command[2]).name == "modelryx_freecad_preprocessor.py"
        assert options["check"] is True
        return SimpleNamespace(stdout="native startup log\n" + json.dumps(report) + "\n")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main([str(source), str(output)]) == 0
    assert json.loads(capsys.readouterr().out) == report
    assert len(calls) == 1
    assert source.read_bytes() == b"original"
