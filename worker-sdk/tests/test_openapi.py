import copy
from pathlib import Path

import jsonschema
import pytest
import yaml

SPEC_PATH = Path(__file__).resolve().parents[1] / "contract" / "openapi.yaml"


def test_openapi_job_request_and_invalid_digest():
    spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    assert spec["openapi"] == "3.1.0"
    schema = {
        "$ref": "#/components/schemas/JobRequest",
        "components": spec["components"],
    }
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    request = {
        "contractVersion": "1.0",
        "jobId": "95fc8a83-24cc-409a-b121-b4b951bfbf2c",
        "attempt": 1,
        "cadSystem": "freecad",
        "documentKind": "part",
        "source": {
            "snapshotId": "ed42e268-91bb-48bd-8eb6-74182443c31e",
            "sha256": "a" * 64,
            "size": 100,
            "rootPath": "Part.FCStd",
        },
        "configuration": None,
        "requiredOutputs": ["modelryx-preview"],
        "preferredOutputs": [],
        "expiresAt": "2030-01-01T00:00:00Z",
    }
    validator.validate(request)
    invalid = copy.deepcopy(request)
    invalid["source"]["sha256"] = "not-a-digest"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)


def test_openapi_required_endpoints_and_security():
    spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    assert spec["security"] == [{"WorkerKey": []}]
    assert (
        spec["components"]["securitySchemes"]["WorkerKey"]["name"]
        == "X-Modelryx-Worker-Key"
    )
    assert spec["paths"]["/health"]["get"]["security"] == []
    assert spec["paths"]["/ready"]["get"]["security"] == []
    assert set(spec["paths"]) >= {
        "/api/v1/capabilities",
        "/api/v1/jobs",
        "/api/v1/jobs/{jobId}",
        "/api/v1/jobs/{jobId}/cancel",
        "/api/v1/jobs/{jobId}/outputs/{role}",
    }
    schema = {"$ref": "#/components/schemas/Capabilities", "components": spec["components"]}
    jsonschema.Draft202012Validator(schema).validate({
        "contractVersion": "1.0",
        "cadSystem": "freecad",
        "documentKinds": ["part"],
        "sourceExtensions": [".fcstd"],
        "outputRoles": ["modelryx-preview"],
    })
