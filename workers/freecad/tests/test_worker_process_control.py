from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))
import modelryx_process_control as process_control


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setitem(sys.modules, "FreeCAD", SimpleNamespace())
    spec = importlib.util.spec_from_file_location(
        "test_freecad_adapter", ROOT / "modelryx_freecad_adapter.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunningProcess:
    pid = 123
    returncode = None

    def poll(self):
        return None

    def communicate(self, timeout):
        raise subprocess.TimeoutExpired("converter", timeout)


@pytest.mark.parametrize("cancelled", [False, True])
def test_conversion_terminates_group_on_timeout_or_cancellation(
    adapter, monkeypatch, tmp_path, cancelled
):
    process = RunningProcess()
    launches = []
    stopped = []
    monkeypatch.setattr(
        adapter.subprocess, "Popen", lambda *args, **kwargs: launches.append(kwargs) or process
    )
    monkeypatch.setattr(adapter, "isolated_process_options", lambda: {"start_new_session": True})
    monkeypatch.setattr(adapter, "terminate_process_tree", stopped.append)

    def cancel():
        if launches:
            raise RuntimeError("cancelled")

    expected = RuntimeError if cancelled else subprocess.TimeoutExpired
    with pytest.raises(expected):
        adapter.FreeCADWorkerAdapter().convert(
            tmp_path / "input.FCStd",
            tmp_path / "output.pfg",
            timeout_seconds=100 if cancelled else 0,
            max_nodes=10,
            max_triangles=10,
            progress_callback=None,
            cancellation_callback=cancel if cancelled else None,
        )
    assert launches[0]["start_new_session"] is True
    assert stopped == [process]


def test_execute_plan_terminates_group_on_timeout(adapter, monkeypatch, tmp_path):
    process = RunningProcess()
    stopped = []
    monkeypatch.setattr(adapter.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(adapter, "terminate_process_tree", stopped.append)
    with pytest.raises(subprocess.TimeoutExpired):
        adapter.FreeCADWorkerAdapter().execute_plan(
            tmp_path / "plan.json",
            tmp_path / "source.FCStd",
            tmp_path / "out.FCStd",
            tmp_path / "report.json",
            timeout_seconds=0,
        )
    assert stopped == [process]


def test_stage_timeout_applies_without_progress_callback(adapter, monkeypatch, tmp_path):
    process = RunningProcess()
    stopped = []
    ticks = iter([0, 0, 1, 3])
    monkeypatch.setenv("MODELRYX_FREECAD_STAGE_TIMEOUT_SECONDS", "2")
    monkeypatch.setattr(adapter, "terminate_process_tree", stopped.append)
    monkeypatch.setattr(adapter.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(adapter.time, "sleep", lambda _duration: None)
    output = tmp_path / "output.pfg"
    def launch(*args, **kwargs):
        output.with_suffix(".progress.json").write_text(
            json.dumps({"stage": "RECOMPUTING_DOCUMENT", "progress": 45, "message": "Recomputing"})
        )
        return process

    monkeypatch.setattr(adapter.subprocess, "Popen", launch)
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        adapter.FreeCADWorkerAdapter().convert(
            tmp_path / "input.FCStd",
            output,
            timeout_seconds=100,
            max_nodes=10,
            max_triangles=10,
            progress_callback=None,
            cancellation_callback=None,
        )
    assert "RECOMPUTING_DOCUMENT" in failure.value.output
    assert stopped == [process]


def test_cancelled_edit_is_not_launched(adapter, monkeypatch, tmp_path):
    def cancel():
        raise RuntimeError("cancelled")

    def launch(*args, **kwargs):
        pytest.fail("cancelled work was launched")

    monkeypatch.setattr(adapter.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="cancelled"):
        adapter.FreeCADWorkerAdapter().execute_plan(
            tmp_path / "plan", tmp_path / "source", tmp_path / "out", tmp_path / "report",
            timeout_seconds=100, cancellation_callback=cancel,
        )


def test_running_edit_cancellation_terminates_process(adapter, monkeypatch, tmp_path):
    process = RunningProcess()
    stopped = []
    calls = []
    monkeypatch.setattr(adapter.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(adapter, "terminate_process_tree", stopped.append)

    def cancel():
        calls.append(True)
        if len(calls) > 1:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        adapter.FreeCADWorkerAdapter().execute_plan(
            tmp_path / "plan", tmp_path / "source", tmp_path / "out", tmp_path / "report",
            timeout_seconds=100, cancellation_callback=cancel,
        )
    assert stopped == [process]


def test_progress_callback_failure_is_not_swallowed(adapter, monkeypatch, tmp_path):
    process = RunningProcess()
    stopped = []
    progress = tmp_path / "progress.json"

    def launch(*args, **kwargs):
        progress.write_text(json.dumps({"stage": "EXPORTING", "progress": 50, "message": "Mesh"}))
        return process

    def callback(*args):
        raise ValueError("database update failed")

    monkeypatch.setattr(adapter.subprocess, "Popen", launch)
    monkeypatch.setattr(adapter, "terminate_process_tree", stopped.append)
    with pytest.raises(ValueError, match="database update failed"):
        adapter._run_process(
            ["native"], tmp_path / "result", timeout_seconds=100, cancellation_callback=None,
            progress_path=progress, progress_callback=callback,
        )
    assert stopped == [process]


def test_stale_progress_is_removed_and_log_errors_are_bounded(adapter, monkeypatch, tmp_path):
    progress = tmp_path / "progress.json"
    progress.write_text("stale")

    def launch(*args, **kwargs):
        assert not progress.exists()
        kwargs["stderr"].write(b"x" * 100_000 + b"native error")
        return SimpleNamespace(poll=lambda: 1, returncode=1)

    monkeypatch.setattr(adapter.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="native error") as failure:
        adapter._run_process(
            ["native"], tmp_path / "result", timeout_seconds=100, cancellation_callback=None,
            progress_path=progress,
        )
    assert len(str(failure.value)) == adapter.ERROR_TAIL_BYTES


def test_excessive_native_logging_terminates_process(adapter, monkeypatch, tmp_path):
    process = RunningProcess()
    stopped = []
    monkeypatch.setattr(adapter, "MAX_LOG_BYTES", 10)

    def launch(*args, **kwargs):
        kwargs["stdout"].write(b"x" * 11)
        kwargs["stdout"].flush()
        return process

    monkeypatch.setattr(adapter.subprocess, "Popen", launch)
    monkeypatch.setattr(adapter, "terminate_process_tree", stopped.append)
    with pytest.raises(RuntimeError, match="log exceeded"):
        adapter._run_process(
            ["native"], tmp_path / "result", timeout_seconds=100, cancellation_callback=None,
        )
    assert stopped == [process]


def test_cancellation_is_checked_after_native_process_exits(adapter, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        adapter.subprocess, "Popen",
        lambda *args, **kwargs: SimpleNamespace(poll=lambda: 0, returncode=0),
    )
    def cancel():
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("cancelled at exit")

    with pytest.raises(RuntimeError, match="cancelled at exit"):
        adapter._run_process(
            ["native"], tmp_path / "result", timeout_seconds=100, cancellation_callback=cancel,
        )

@pytest.mark.parametrize("value", ["0", "-1", "nan", "invalid"])
def test_invalid_stage_timeout_is_rejected(adapter, monkeypatch, value):
    monkeypatch.setenv("MODELRYX_FREECAD_STAGE_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="positive number"):
        adapter._stage_timeout_seconds()


def test_windows_tree_kill_failure_falls_back_to_leader(monkeypatch):
    calls = []
    process = SimpleNamespace(
        pid=123,
        poll=lambda: None,
        kill=lambda: calls.append("kill"),
        wait=lambda timeout: calls.append("wait"),
    )
    monkeypatch.setattr(process_control.os, "name", "nt")
    monkeypatch.setattr(
        process_control.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1)
    )
    process_control.terminate_process_tree(process)
    assert calls == ["kill", "wait"]
