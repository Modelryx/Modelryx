import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not PWSH, reason="PowerShell is required")


@pytest.fixture
def project(tmp_path):
    for relative in (
        "scripts/configure-project.ps1",
        ".env.example",
        "server/.env.example",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    return tmp_path


def configure(project, *arguments):
    return subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(project / "scripts/configure-project.ps1"),
            "-NonInteractive",
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def env_file(path):
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def test_fresh_production_setup_and_repeat_preserve_credentials(project):
    result = configure(project, "-Environment", "production", "-PostgresPort", "55432")
    assert result.returncode == 0, result.stderr
    root = env_file(project / ".env")
    server = env_file(project / "server/.env")
    assert len(root["POSTGRES_PASSWORD"]) >= 16
    assert len(root["MODELRYX_BOOTSTRAP_ADMIN_PASSWORD"]) >= 16
    assert server["MODELRYX_DATABASE_URL"] == (
        f"postgresql+psycopg://modelryx:{root['POSTGRES_PASSWORD']}@127.0.0.1:55432/modelryx"
    )
    assert server["MODELRYX_ENVIRONMENT"] == "production"
    assert configure(project).returncode == 0
    again = env_file(project / ".env")
    for key in (
        "POSTGRES_PASSWORD",
        "MODELRYX_JWT_SECRET",
        "MODELRYX_INTERNAL_WORKER_KEY",
        "MODELRYX_BOOTSTRAP_ADMIN_PASSWORD",
    ):
        assert again[key] == root[key]


def test_rejects_weak_production_password_before_writing(project):
    result = configure(project, "-Environment", "production", "-AdminPassword", "admin")
    assert result.returncode != 0
    assert not (project / ".env").exists()


def test_agent_install_is_explicit_and_receives_freecad_path(project):
    agent = project / "local_cad_agent"
    agent.mkdir()
    (agent / "install-windows.ps1").write_text(
        "param([string]$FreeCadCommand)\nSet-Content -LiteralPath "
        '(Join-Path $PSScriptRoot "called.txt") -Value $FreeCadCommand\n',
        encoding="utf-8",
    )
    executable = project / "freecad.exe"
    executable.touch()
    result = configure(project, "-FreeCadCmd", str(executable), "-InstallLocalCadAgent")
    assert result.returncode == 0, result.stderr
    assert (agent / "called.txt").read_text(encoding="utf-8-sig").strip() == str(
        executable
    )


def test_optional_settings_and_explicit_database_are_preserved(project):
    result = configure(project, "-CadAnalyzerUrl", "https://analyzer.example")
    assert result.returncode == 0, result.stderr
    root = env_file(project / ".env")
    assert root["MODELRYX_FREECAD_PREPROCESSOR_URL"] == "https://analyzer.example"
    server_path = project / "server/.env"
    server_path.write_text(
        server_path.read_text(encoding="utf-8").replace(
            env_file(server_path)["MODELRYX_DATABASE_URL"],
            "postgresql://custom.example/db",
        ),
        encoding="utf-8",
    )
    assert configure(project).returncode == 0
    assert env_file(server_path)["MODELRYX_DATABASE_URL"] == "postgresql://custom.example/db"


def test_existing_analyzer_url_is_preserved_as_preprocessor_url(project):
    (project / ".env").write_text(
        "MODELRYX_CAD_ANALYZER_URL=https://old-preprocessor.example\n", encoding="utf-8"
    )
    result = configure(project)
    assert result.returncode == 0, result.stderr
    assert env_file(project / ".env")["MODELRYX_FREECAD_PREPROCESSOR_URL"] == (
        "https://old-preprocessor.example"
    )
