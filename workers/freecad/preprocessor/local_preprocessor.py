"""Generic Local CAD Agent entry point: package dependencies and extract local metadata."""
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise ValueError("Expected source.FCStd and output directory")
    source, output = (Path(value).resolve() for value in args)
    base = Path(__file__).resolve().parent
    result = subprocess.run([sys.executable, "-u", str(base / "preprocessor/modelryx_freecad_preprocessor.py"),
                             str(source), str(output)], capture_output=True, text=True, check=True)
    report = json.loads(next(line for line in reversed(result.stdout.splitlines()) if line.startswith("{")))
    # Missing references are returned to the agent for actionable source repair.
    # Metadata comes only from the isolated package whose links have been rebound.
    if not report.get("missing_references") and report.get("isolated_reopen_validated"):
        destination = output / "metadata-package"
        destination.mkdir()
        with ZipFile(report["package"]) as package:
            if len(package.infolist()) > 500 or sum(entry.file_size for entry in package.infolist()) > 2 * 1024**3:
                raise ValueError("Dependency package exceeds local limits")
            for entry in package.infolist():
                target = (destination / entry.filename).resolve()
                if not target.is_relative_to(destination) or "\\" in entry.filename:
                    raise ValueError("Unsafe dependency package path")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(entry) as stream, target.open("xb") as file:
                        import shutil
                        shutil.copyfileobj(stream, file)
        root = (destination / report["root_logical_path"]).resolve()
        if not root.is_relative_to(destination) or not root.is_file():
            raise ValueError("Package root is missing or unsafe")
        subprocess.run([sys.executable, "-u", str(base / "converter/fcstd_to_modelryx_glb.py"), str(root),
                        "--metadata-only", "--metadata-output", str(output / "part-metadata.json")],
                       stdout=sys.stderr, check=True)
    print(json.dumps(report, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
