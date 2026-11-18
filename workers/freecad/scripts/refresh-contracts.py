"""Explicitly refresh this worker release's contract snapshots from a platform checkout."""

import argparse
import ast
import hashlib
import json
import tomllib
from pathlib import Path


INPUTS = {
    "worker-sdk/pyproject.toml": "vendor/worker-sdk/pyproject.toml",
    "worker-sdk/LICENSE": "vendor/worker-sdk/LICENSE",
    "worker-sdk/src/modelryx_worker_sdk/__init__.py":
        "vendor/worker-sdk/src/modelryx_worker_sdk/__init__.py",
    "server/src/server/modelryx/metadata/schemas/part_metadata_v1.json":
        "contracts/part_metadata_v1.json",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-root", type=Path, required=True)
    args = parser.parse_args()
    worker = Path(__file__).resolve().parents[1]
    # Read every input before replacing anything; an incomplete checkout cannot partially refresh.
    data = {target: (source, (args.platform_root / source).read_bytes())
            for source, target in INPUTS.items()}
    sdk = data["vendor/worker-sdk/src/modelryx_worker_sdk/__init__.py"][1]
    version = next(ast.literal_eval(node.value) for node in ast.parse(sdk).body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "CONTRACT_VERSION"
                           for t in node.targets))
    package = tomllib.loads(data["vendor/worker-sdk/pyproject.toml"][1].decode("utf-8"))
    schema = json.loads(data["contracts/part_metadata_v1.json"][1])
    manifest = {"contractVersion": version, "sdkVersion": package["project"]["version"],
                "partMetadataVersion": schema["properties"]["schema_version"]["const"],
                "files": []}
    for target, (source, content) in data.items():
        path = worker / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        manifest["files"].append({"path": target, "source": source,
                                  "sha256": hashlib.sha256(content).hexdigest()})
    (worker / "contracts/snapshots.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Refreshed worker-owned snapshots. Review the diff and run FreeCAD CI before release.")


if __name__ == "__main__":
    main()
