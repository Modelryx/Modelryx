"""Native CI failures must remain failures and retain container OOM evidence."""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux CI shell wrapper")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("exit_code,oom", [(0, False), (1, False), (137, True), (137, False)])
def test_native_gate_retains_failure_and_cleans_up_its_container(tmp_path, exit_code, oom):
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text("""#!/usr/bin/env python
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[0] == 'create':
    Path(args[args.index('--cidfile') + 1]).write_text('test-container')
    Path(os.environ['CREATED_ARGS']).write_text(json.dumps(args))
elif args[0] == 'start':
    print('native fixture output', flush=True)
    sys.exit(int(os.environ['NATIVE_EXIT']))
elif args[0] == 'inspect':
    pattern = args[args.index('--format') + 1]
    if pattern == '{{.State.ExitCode}}': print(os.environ['NATIVE_EXIT'])
    elif pattern == '{{.State.OOMKilled}}': print(os.environ['NATIVE_OOM'])
    else: print(json.dumps({'ExitCode': int(os.environ['NATIVE_EXIT']),
                           'OOMKilled': os.environ['NATIVE_OOM'] == 'true'}))
elif args[0] == 'rm':
    Path(os.environ['REMOVED']).write_text(args[-1])
else: raise RuntimeError(args)
""", encoding="utf-8")
    docker.chmod(0o755)
    environment = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                   "NATIVE_EXIT": str(exit_code), "NATIVE_OOM": str(oom).lower(),
                   "REMOVED": str(tmp_path / "removed"),
                   "CREATED_ARGS": str(tmp_path / "created")}
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/run-native-gate.sh"), "/app/fixture.py", "test-image",
         "--compatibility-candidate"],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert '"OOMKilled": ' + str(oom).lower() in result.stdout
    assert (tmp_path / "removed").read_text() == "test-container"
    import json

    arguments = json.loads((tmp_path / "created").read_text())
    assert arguments[-5:] == ["test-image", "python", "-u", "/app/fixture.py",
                             "--compatibility-candidate"]
    if oom:
        assert "OOMKilled=true" in result.stdout
    elif exit_code == 137:
        assert "did not report an OOM kill" in result.stdout


def test_runner_resource_setup_refuses_developer_host():
    result = subprocess.run(["bash", str(ROOT / "scripts/ci-memory.sh"), "prepare"],
                            env={**os.environ, "GITHUB_ACTIONS": "false"},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "GitHub Actions runners only" in result.stderr


def test_swap_cleanup_refuses_unowned_file(tmp_path):
    file = tmp_path / "not-a-worker-swap-file"
    file.write_text("keep")
    result = subprocess.run(["bash", str(ROOT / "scripts/ci-memory.sh"), "cleanup"],
                            env={**os.environ, "GITHUB_ACTIONS": "true",
                                 "RUNNER_TEMP": str(tmp_path), "FREECAD_CI_SWAPFILE": str(file)},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert file.read_text() == "keep"
