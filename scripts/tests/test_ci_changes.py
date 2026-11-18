"""Exercise docs-only policy and GitHub event comparison ranges."""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "ci_changes", Path(__file__).resolve().parents[1] / "ci_changes.py",
)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


@pytest.mark.parametrize("paths", [
    ["README.md"],
    ["DEVELOPMENT.md", "docs/operations/deployment.md"],
    ["docs/images/setup.png", "docs/guide.rst", ".github/WORKFLOWS.md"],
    ["licenses/third-party/README.md", "workers/freecad/README.md"],
])
def test_only_non_build_documentation_can_skip(paths):
    assert not ci.requires_ci(paths)


@pytest.mark.parametrize("path", [
    "server/README.md", "worker-sdk/README.md", "LICENSE", ".gitattributes",
    "licenses/third-party/npm-react-LICENSE.txt", "licenses/third-party/sources.json",
    "compose.yaml", ".env.example", ".github/workflows/ci.yml",
    "scripts/ci_changes.py", "front-end/package-lock.json", "server/src/main.py",
    "docs/requirements.txt", "docs/example.py", "workers/freecad/fixture.svg",
])
def test_build_inputs_and_unknown_files_run_ci(path):
    assert ci.requires_ci(["README.md", path])


def test_empty_change_set_does_not_waive_checks():
    assert ci.requires_ci([])


@pytest.fixture
def repository(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("Git is required")
    monkeypatch.chdir(tmp_path)

    def git(*arguments):
        return subprocess.run(
            ["git", "-c", "user.name=CI Test", "-c", "user.email=ci@example.invalid",
             *arguments], check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init", "--quiet")
    return git


def commit(git, name, content):
    path = Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git("add", "--all")
    git("commit", "--quiet", "-m", "fixture")
    return git("rev-parse", "HEAD")


def test_push_checks_all_commits_not_only_last_commit(repository):
    git = repository
    base = commit(git, "README.md", "base")
    commit(git, "server/source.py", "code change")
    head = commit(git, "README.md", "docs follow-up")
    paths = ci.changed_paths("push", {"before": base, "after": head})
    assert ci.requires_ci(paths)
    assert "server/source.py" in paths


def test_pr_uses_merge_base_and_includes_all_proposed_changes(repository):
    git = repository
    base = commit(git, "README.md", "base")
    git("checkout", "-b", "target")
    target = commit(git, "server/source.py", "unrelated base-branch change")
    git("checkout", "-b", "feature", base)
    head = commit(git, "README.md", "documentation")
    event = {"pull_request": {"base": {"sha": target}, "head": {"sha": head}}}
    assert ci.changed_paths("pull_request", event) == ["README.md"]
    assert not ci.requires_ci(ci.changed_paths("pull_request", event))
    head = commit(git, "worker-sdk/source.py", "proposed code")
    event["pull_request"]["head"]["sha"] = head
    assert ci.requires_ci(ci.changed_paths("pull_request", event))


def test_renaming_build_readme_to_docs_does_not_skip(repository):
    git = repository
    base = commit(git, "server/README.md", "build metadata")
    git("mv", "server/README.md", "README.md")
    git("commit", "--quiet", "-m", "move")
    paths = ci.changed_paths("push", {"before": base, "after": git("rev-parse", "HEAD")})
    assert set(paths) == {"server/README.md", "README.md"}
    assert ci.requires_ci(paths)


@pytest.mark.parametrize("event", [
    {"before": "0" * 40, "after": "a" * 40},
    {"before": "unavailable", "after": "unavailable"},
    {},
])
def test_missing_history_or_event_always_runs_full_ci(tmp_path, monkeypatch, event):
    event_file = tmp_path / "event.json"
    output = tmp_path / "output"
    event_file.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    def unavailable_git(*_args):
        raise OSError("Comparison history is unavailable")

    monkeypatch.setattr(ci, "git", unavailable_git)
    ci.main()
    assert output.read_text(encoding="utf-8") == "run_ci=true\n"


def test_docs_only_event_emits_false(tmp_path, monkeypatch):
    event_file = tmp_path / "event.json"
    output = tmp_path / "output"
    event_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(ci, "changed_paths", lambda *args: ["docs/guide.md"])
    ci.main()
    assert output.read_text(encoding="utf-8") == "run_ci=false\n"
