"""Verify isolated process-tree cleanup on the Linux FreeCAD worker image."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from modelryx_process_control import isolated_process_options, terminate_process_tree


def verify_adapter_cancellation() -> None:
    from modelryx_freecad_adapter import _run_process

    with tempfile.TemporaryDirectory(prefix="modelryx-cancel-") as directory:
        root = Path(directory)
        ready, survived = root / "ready", root / "survived"
        child = (
            "from pathlib import Path; import time; "
            f"Path({str(ready)!r}).touch(); time.sleep(2); Path({str(survived)!r}).touch()"
        )
        parent = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"
        )
        class Cancelled(RuntimeError):
            pass

        def cancel():
            if ready.exists():
                raise Cancelled("authorized cancellation")

        try:
            _run_process(
                [sys.executable, "-c", parent], root / "native.json",
                timeout_seconds=10, cancellation_callback=cancel,
            )
        except Cancelled:
            pass
        else:
            raise AssertionError("native edit ignored cancellation")
        time.sleep(2.2)
        assert not survived.exists(), "native child survived adapter cancellation"


def main() -> int:
    child_code = (
        "import time; print('READY', flush=True); time.sleep(2); print('SURVIVED', flush=True)"
    )
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **isolated_process_options(),
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
        terminate_process_tree(process)
        stdout, _stderr = process.communicate(timeout=5)
        assert "SURVIVED" not in stdout
        assert process.returncode is not None
    finally:
        if process.poll() is None:
            terminate_process_tree(process)
    verify_adapter_cancellation()
    print("FreeCAD worker process-tree integration test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
