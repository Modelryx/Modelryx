"""Generic Local CAD Agent entry point: package native dependencies for server-side extraction."""
import json
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise ValueError("Expected source.FCStd and output directory")
    source, output = (Path(value).resolve() for value in args)
    base = Path(__file__).resolve().parent
    result = subprocess.run([sys.executable, "-u", str(base / "preprocessor/modelryx_freecad_preprocessor.py"),
                             str(source), str(output)], capture_output=True, text=True, check=True)
    report = json.loads(next(line for line in reversed(result.stdout.splitlines()) if line.startswith("{")))
    print(json.dumps(report, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
