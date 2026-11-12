"""Black-box conformance probe for a standalone CAD worker.

This submits one real source file and creates a worker-local job.
"""

import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from modelryx_worker_sdk import validate_result

TERMINAL = {"ReadyToPublish", "Partial", "Failed", "Cancelled", "Expired"}


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run(args):
    key = os.environ.get(args.key_env)
    if not key:
        raise ValueError(f"Set {args.key_env} to the worker credential")
    headers = {"X-Modelryx-Worker-Key": key}
    base = args.url.rstrip("/")
    source = args.source
    request = {
        "contractVersion": "1.0",
        "jobId": str(uuid.uuid4()),
        "attempt": 1,
        "cadSystem": args.cad_system,
        "documentKind": args.document_kind,
        "source": {
            "snapshotId": str(uuid.uuid4()),
            "sha256": digest_file(source),
            "size": source.stat().st_size,
            "rootPath": args.root,
        },
        "configuration": None,
        "requiredOutputs": list(args.output),
        "preferredOutputs": [],
        "expiresAt": (datetime.now(UTC) + timedelta(seconds=args.timeout)).isoformat(),
    }
    roles = dict(item.split("=", 1) for item in args.output)
    request["requiredOutputs"] = list(roles)
    allowed_outputs = {
        role: (media, args.max_output_bytes) for role, media in roles.items()
    }
    job_path = f"/api/v1/jobs/{request['jobId']}"
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        assert client.get(base + "/health").status_code == 200
        assert client.get(base + "/ready").status_code == 200
        assert client.get(base + "/api/v1/capabilities").status_code == 401
        capabilities_response = client.get(
            base + "/api/v1/capabilities", headers=headers
        )
        capabilities_response.raise_for_status()
        capabilities = capabilities_response.json()
        assert capabilities["contractVersion"] == request["contractVersion"]
        assert capabilities["cadSystem"] == request["cadSystem"]
        assert request["documentKind"] in capabilities["documentKinds"]
        assert set(roles).issubset(capabilities["outputRoles"])

        def submit(payload):
            with source.open("rb") as stream:
                return client.post(
                    base + "/api/v1/jobs",
                    headers=headers,
                    files={
                        "request": (
                            "request.json",
                            json.dumps(payload),
                            "application/json",
                        ),
                        "source": (source.name, stream, args.media_type),
                    },
                )

        response = submit(request)
        assert response.status_code == 202, response.text
        assert response.headers["location"] == job_path
        replay = submit(request)
        assert replay.status_code == 202 and replay.json()["id"] == request["jobId"]
        conflict = submit({**request, "attempt": 2})
        assert conflict.status_code == 409
        deadline = time.monotonic() + args.timeout
        while True:
            response = client.get(base + job_path, headers=headers)
            response.raise_for_status()
            record = response.json()
            assert record["id"] == request["jobId"]
            assert type(record["progress"]) is int and 0 <= record["progress"] <= 100
            if record["state"] in TERMINAL:
                break
            if time.monotonic() >= deadline:
                client.post(base + job_path + "/cancel", headers=headers)
                raise TimeoutError("Worker did not reach a terminal state")
            time.sleep(1)
        assert record["state"] in {"ReadyToPublish", "Partial"}, record
        result = record["result"]
        validate_result(request, result, allowed_outputs=allowed_outputs)
        assert record["state"] == (
            "Partial" if result["warnings"] else "ReadyToPublish"
        )
        for output in result["outputs"]:
            assert client.get(base + output["downloadUrl"]).status_code == 401
            digest = hashlib.sha256()
            size = 0
            with client.stream(
                "GET", base + output["downloadUrl"], headers=headers
            ) as response:
                response.raise_for_status()
                assert (
                    response.headers["content-type"].split(";", 1)[0]
                    == output["mediaType"]
                )
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > output["size"]:
                        raise ValueError(
                            f"Output {output['role']} exceeds its declared size"
                        )
                    digest.update(chunk)
            assert size == output["size"]
            assert digest.hexdigest() == output["sha256"].lower()
        assert (
            client.post(base + job_path + "/cancel", headers=headers).status_code == 202
        )
        assert (
            client.get(base + job_path, headers=headers).json()["state"]
            == record["state"]
        )
    print(f"Worker conformance passed for job {request['jobId']}: {record['state']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--root", required=True, help="Package-relative root document path"
    )
    parser.add_argument("--cad-system", required=True)
    parser.add_argument("--document-kind", required=True)
    parser.add_argument(
        "--output", action="append", required=True, metavar="ROLE=MEDIA_TYPE"
    )
    parser.add_argument("--media-type", default="application/octet-stream")
    parser.add_argument("--key-env", default="MODELRYX_INTERNAL_WORKER_KEY")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--max-output-bytes", type=int, default=2 * 1024**3)
    args = parser.parse_args()
    if args.timeout <= 0 or args.max_output_bytes <= 0:
        parser.error("timeout and output limit must be positive")
    if any(
        "=" not in output or not all(output.split("=", 1)) for output in args.output
    ):
        parser.error("each output must have the form ROLE=MEDIA_TYPE")
    if len({output.split("=", 1)[0] for output in args.output}) != len(args.output):
        parser.error("output roles must be unique")
    run(args)


if __name__ == "__main__":
    main()
