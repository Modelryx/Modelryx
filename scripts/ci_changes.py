"""Classify platform changes; independent FreeCAD validation owns its directory."""

import json
import os
import subprocess
from pathlib import PurePosixPath


# These Markdown files are copied into images or used as package metadata.
BUILD_READMES = {"server/README.md", "worker-sdk/README.md"}
DOC_ASSETS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf", ".rst"}


def requires_ci(paths):
    if not paths:
        return True
    for path in paths:
        if path.startswith("workers/freecad/"):
            continue
        suffix = PurePosixPath(path).suffix.lower()
        if path in BUILD_READMES:
            return True
        if suffix == ".md":
            continue
        if path.startswith("docs/") and suffix in DOC_ASSETS:
            continue
        return True
    return False


def git(*arguments):
    return subprocess.run(
        ["git", *arguments], check=True, capture_output=True,
    ).stdout


def changed_paths(event_name, event):
    if event_name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
        head = event["pull_request"]["head"]["sha"]
        # Compare the complete PR, excluding unrelated changes on the base branch.
        base = git("merge-base", base, head).decode().strip()
    elif event_name == "push":
        base, head = event["before"], event["after"]
        if not base.strip("0") or not head.strip("0"):
            raise ValueError("New/deleted branch: no complete comparison range")
    else:
        raise ValueError("Unknown event: run full CI")
    # Disabling rename detection includes both old and new names, so moving a
    # build input to a documentation path cannot accidentally skip validation.
    raw = git("diff", "--no-renames", "--name-only", "-z", base, head, "--")
    return [name.decode("utf-8", errors="surrogateescape") for name in raw.split(b"\0") if name]


def main():
    run_ci = True
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as stream:
            paths = changed_paths(os.environ["GITHUB_EVENT_NAME"], json.load(stream))
        run_ci = requires_ci(paths)
        print(f"Changed paths: {len(paths)}; run full CI: {str(run_ci).lower()}")
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError):
        # Missing history, force pushes or malformed events must never waive CI.
        print("Could not establish a docs-only change; running full CI.")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"run_ci={str(run_ci).lower()}\n")


if __name__ == "__main__":
    main()
