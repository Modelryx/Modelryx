import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from modelryx.engineering_search.models import SearchRequest
from modelryx.engineering_search.registry import SEARCH_CAPABILITIES


class ScenarioCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class ScenarioCatalog:
    version: str
    digest: str
    scenarios: tuple[dict[str, Any], ...]

    def scenario(self, scenario_id: str) -> dict[str, Any]:
        try:
            return next(value for value in self.scenarios if value["id"] == scenario_id)
        except StopIteration as exc:
            raise ScenarioCatalogError(f"scenario does not exist: {scenario_id}") from exc

    def public(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "digest": self.digest,
            "scenarios": [
                {
                    key: scenario[key]
                    for key in (
                        "id",
                        "version",
                        "status",
                        "priority",
                        "entities",
                        "operation",
                        "capabilities",
                        "canonical_example",
                    )
                }
                for scenario in self.scenarios
                if scenario["status"] == "published"
            ],
        }


def _catalog_root() -> Path:
    return Path(__file__).with_name("catalog")


def _validate_semantics(document: dict[str, Any]) -> None:
    ids = [scenario["id"] for scenario in document["scenarios"]]
    duplicate_ids = sorted({scenario_id for scenario_id in ids if ids.count(scenario_id) > 1})
    if duplicate_ids:
        raise ScenarioCatalogError(f"duplicate scenario IDs: {', '.join(duplicate_ids)}")
    capability_fields = SEARCH_CAPABILITIES["fields"]
    for scenario in document["scenarios"]:
        for capability in scenario["capabilities"]:
            if capability not in capability_fields:
                raise ScenarioCatalogError(
                    f"scenario {scenario['id']} references unknown capability {capability}"
                )
        try:
            SearchRequest.model_validate(scenario["example_request"])
        except ValueError as exc:
            raise ScenarioCatalogError(
                f"scenario {scenario['id']} has an invalid example request: {exc}"
            ) from exc


def load_scenario_catalog(
    catalog_path: Path | None = None, schema_path: Path | None = None
) -> ScenarioCatalog:
    root = _catalog_root()
    catalog_path = catalog_path or root / "v1" / "catalog.json"
    schema_path = schema_path or root / "schema.json"
    try:
        document = json.loads(catalog_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(document)
        _validate_semantics(document)
    except (OSError, json.JSONDecodeError, JsonSchemaValidationError) as exc:
        raise ScenarioCatalogError(f"scenario catalog cannot be loaded: {exc}") from exc
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return ScenarioCatalog(
        version=document["version"],
        digest=hashlib.sha256(canonical).hexdigest(),
        scenarios=tuple(document["scenarios"]),
    )


@lru_cache(maxsize=1)
def active_scenario_catalog() -> ScenarioCatalog:
    return load_scenario_catalog()
