import hashlib

import pytest

from modelryx_worker_sdk import CONTRACT_VERSION, describe_output, validate_result


@pytest.fixture
def job(tmp_path):
    source = b"source"
    job_id = "95fc8a83-24cc-409a-b121-b4b951bfbf2c"
    request = {
        "contractVersion": CONTRACT_VERSION,
        "jobId": job_id,
        "attempt": 1,
        "cadSystem": "freecad",
        "configuration": None,
        "source": {"sha256": hashlib.sha256(source).hexdigest()},
        "requiredOutputs": ["modelryx-preview"],
    }
    preview = tmp_path / "preview.pfg"
    preview.write_bytes(b"preview")
    output = describe_output(
        job_id,
        "modelryx-preview",
        preview,
        "application/zip",
        generator="freecad-test",
        source_role="PRIMARY",
    )
    result = {
        **{
            key: request[key]
            for key in (
                "contractVersion",
                "jobId",
                "attempt",
                "cadSystem",
                "configuration",
            )
        },
        "sourceSha256": request["source"]["sha256"],
        "adapterVersion": "test",
        "status": "complete",
        "warnings": [],
        "outputs": [output],
    }
    return request, result


def test_accepts_finalized_preview(job):
    request, result = job
    output = result["outputs"][0]
    assert output["sha256"] == hashlib.sha256(b"preview").hexdigest()
    assert output["size"] == len(b"preview")
    validate_result(
        request, result, allowed_outputs={"modelryx-preview": ("application/zip", 100)}
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("downloadUrl", "https://other.example/output"),
        (
            "downloadUrl",
            "/api/v1/jobs/ed42e268-91bb-48bd-8eb6-74182443c31e/outputs/modelryx-preview",
        ),
        ("mediaType", "application/json"),
        ("size", 101),
        ("size", True),
        ("sha256", "bad"),
        ("provenance", {"generator": "freecad-test"}),
    ],
)
def test_rejects_invalid_output_manifest(job, field, value):
    request, result = job
    result["outputs"][0][field] = value
    with pytest.raises(ValueError):
        validate_result(
            request,
            result,
            allowed_outputs={"modelryx-preview": ("application/zip", 100)},
        )


def test_partial_result_still_requires_outputs(job):
    request, result = job
    result.update(
        status="partial", warnings=["Optional drawing preview unavailable"], outputs=[]
    )
    with pytest.raises(ValueError, match="omitted required outputs"):
        validate_result(
            request,
            result,
            allowed_outputs={"modelryx-preview": ("application/zip", 100)},
        )
