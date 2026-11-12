"""Export tracked public source into a new directory without Git history.

Stage new public files before exporting, or opt in to including untracked files.
Deleted files are omitted.
The resulting directory requires review before it is published.
"""

import argparse
import shutil
import subprocess
from pathlib import Path

EXCLUDED_ROOTS = {"autocad_worker", "inventor_worker", "nx_worker"}
EXCLUDED_FILES = {".github/workflows/autocad-native.yml"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New, empty export directory")
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="Also export files not ignored by Git that have not been staged",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = args.destination.resolve()
    if (
        destination == root
        or destination == root / ".git"
        or root / ".git" in destination.parents
    ):
        parser.error(
            "destination must not be the source repository or its Git directory"
        )
    if destination.exists():
        parser.error("destination must not already exist")
    command = ["git", "-C", str(root), "ls-files", "-z", "--cached"]
    if args.include_untracked:
        command.extend(["--others", "--exclude-standard"])
    tracked = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.split(b"\0")
    paths = sorted({Path(raw.decode("utf-8")) for raw in tracked if raw})
    for relative in paths:
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.parts[0] in EXCLUDED_ROOTS
            or relative.as_posix() in EXCLUDED_FILES
        ):
            continue
        source = root / relative
        if not source.exists():
            continue
        if (
            source.is_symlink()
            or not source.is_file()
            or not source.resolve().is_relative_to(root)
        ):
            raise RuntimeError(f"Export requires a regular file: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        shutil.copymode(source, target)
    print(f"Exported tracked public files to {destination}. Review before publication.")


if __name__ == "__main__":
    main()
