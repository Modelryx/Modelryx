import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def exporter():
    spec = importlib.util.spec_from_file_location(
        "export_public", Path(__file__).resolve().parents[1] / "export-public.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_excludes_private_workers_and_git_history(
    exporter, monkeypatch, tmp_path
):
    paths = b"LICENSE\0worker-sdk/README.md\0nx_worker/README.md\0"
    monkeypatch.setattr(
        exporter.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=paths)
    )
    destination = tmp_path / "public"
    monkeypatch.setattr(sys, "argv", ["export-public.py", str(destination)])
    exporter.main()
    assert (destination / "LICENSE").is_file()
    assert (destination / "worker-sdk/README.md").is_file()
    assert not (destination / "nx_worker").exists()
    assert not (destination / ".git").exists()


def test_export_refuses_existing_destination(exporter, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["export-public.py", str(tmp_path)])
    with pytest.raises(SystemExit):
        exporter.main()
