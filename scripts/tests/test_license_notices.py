"""License provenance must survive Linux and Windows Git checkouts."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def assert_license_hashes(root):
    directory = root / "licenses/third-party"
    sources = json.loads((directory / "sources.json").read_text(encoding="utf-8"))
    for filename, record in sources.items():
        actual = hashlib.sha256((directory / filename).read_bytes()).hexdigest()
        assert actual == record["sha256"], filename


def test_license_texts_match_recorded_upstream_bytes():
    assert_license_hashes(ROOT)


@pytest.mark.skipif(not shutil.which("git"), reason="Git is required")
@pytest.mark.parametrize("autocrlf", ["false", "true"])
def test_license_hashes_survive_git_checkout(tmp_path, autocrlf):
    repository = tmp_path / "repository"
    checkout = tmp_path / "checkout"
    repository.mkdir()
    shutil.copyfile(ROOT / ".gitattributes", repository / ".gitattributes")
    shutil.copytree(ROOT / "licenses/third-party", repository / "licenses/third-party")

    def git(*arguments):
        subprocess.run(
            ["git", "-C", str(repository), "-c", f"core.autocrlf={autocrlf}", *arguments],
            check=True,
            capture_output=True,
        )

    git("init", "--quiet")
    git("add", ".gitattributes", "licenses/third-party")
    git("checkout-index", "--all", f"--prefix={checkout.as_posix()}/")
    assert_license_hashes(checkout)
