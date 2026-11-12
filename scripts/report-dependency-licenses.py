"""List declared license metadata for the current Python and Conda environment."""

import json
import os
from importlib import metadata
from pathlib import Path


def main():
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if not conda_prefix and (Path(os.sys.prefix) / "conda-meta").is_dir():
        conda_prefix = os.sys.prefix
    conda = []
    if conda_prefix:
        for path in sorted((Path(conda_prefix) / "conda-meta").glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            conda.append(
                {
                    "name": record.get("name"),
                    "version": record.get("version"),
                    "license": record.get("license"),
                    "license_family": record.get("license_family"),
                }
            )
    python = []
    for distribution in metadata.distributions():
        info = distribution.metadata
        python.append(
            {
                "name": info.get("Name"),
                "version": info.get("Version"),
                "license_expression": info.get("License-Expression"),
                "license": info.get("License"),
                "license_classifiers": [
                    value for value in info.get_all("Classifier", [])
                    if value.startswith("License ::")
                ],
            }
        )
    print(
        json.dumps(
            {
                "conda": conda,
                "python": sorted(python, key=lambda item: (item["name"] or "").lower()),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
