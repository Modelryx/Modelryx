# STEP worker

Standalone asynchronous STEP processing service shared by Modelryx CAD integrations. It owns
STEP preprocessing, geometry conversion, metadata extraction, preview generation, validation,
and normalized output publication. Build with `docker build -t modelryx-step-worker .` and run with
`docker run --rm -p 8080:8080 -v step-worker-data:/data/jobs modelryx-step-worker`.

Set `MODELRYX_INTERNAL_WORKER_KEY`, then submit with
`curl -H "X-Modelryx-Worker-Key: $KEY" -H "Idempotency-Key: model-123" -F "source=@model.step" http://localhost:8080/api/v1/jobs`, poll the returned
`/api/v1/jobs/{id}`, then download its output links when state is `READY`.

## API surface

Component ZIP bundles may declare `geometryUnits: "millimeter"` in `manifest.json`. For this
contract, every component needs explicit `partId`/`occurrenceId`, `transformUnits: "millimeter"`
and a finite row-major world affine transform. Conversion preserves placement identities and adds
a meter-scale scene parent so mesh coordinates, translations and local edge metadata scale together.
Legacy bundles without `geometryUnits` retain their previous unit behavior.

Authenticated `/capabilities` advertises `component_bundle_input_units: ["millimeter"]` alongside
`component_bundle_to_glb`. External workers require this declaration before submitting assembly bundles.

Within a conversion job, repeated references to the same resolved geometry file reuse one read,
tessellation and edge extraction. Each placement retains its own transform, identity and color;
the cache is discarded after the job. This reduces repeated CAD processing without merging occurrences.

These changes live in this project's `app/main.py`, `converter/component_bundle_to_glb.py` and
`converter/glb_utils.py`. If the running service does not report `component_bundle_input_units`,
it is not running this updated source. From the project root on the server, deploy the current source
with `docker compose up -d --build step-worker`. Restarting an existing image does not rebuild it.

- `GET /health` — liveness probe
- `GET /ready` — readiness probe for script/storage availability
- `GET /metrics` — authenticated Prometheus queue and processing metrics
- `GET /capabilities` — authenticated contract and runtime capability payload
- `POST /api/v1/jobs` — authenticated job submission
- `GET /api/v1/jobs/{id}` — authenticated job status
- `POST /api/v1/jobs/{id}/cancel` — authenticated cancellation request
- `GET /api/v1/jobs/{id}/glb` — authenticated GLB download when `READY`
- `GET /api/v1/jobs/{id}/metadata` — authenticated metadata download when `READY`
- `GET /api/v1/jobs/{id}/preview` — authenticated structure-preview download when `READY`
- `GET /api/v1/jobs/{id}/raw-metadata` — authenticated raw STEP metadata download when `READY`

### Job states

`QUEUED` → `PROCESSING` → `READY`

Terminal alternatives:

- `FAILED`
- `CANCELLED`

Completed and failed jobs are removed after `STEP_WORKER_RETENTION_SECONDS` (24 hours by default).
# Queue reliability

Queued and in-progress jobs are persisted and resumed after a worker restart. Transient
I/O and timeout failures are retried with exponential backoff. Configure this with
`STEP_WORKER_MAX_ATTEMPTS` (default `3`) and `STEP_WORKER_RETRY_DELAY_SECONDS`
(default `2`).

The executor accepts at most `STEP_WORKER_CONCURRENCY + STEP_WORKER_QUEUE_CAPACITY`
active and waiting jobs (queue capacity defaults to `32`). Additional submissions receive
HTTP `429` with `Retry-After`. Supplying `Idempotency-Key` (1–128 characters) makes a retried
submission return the original durable job instead of creating another conversion.

## Verification

The CI STEP-worker job runs the API and converter contract suites without OpenCascade:

```sh
python -m pytest -q tests/test_api_contract.py tests/test_component_bundle_api.py tests/test_component_bundle_units.py tests/test_glb_utils.py
```

Run from `workers/step/` with the API/test, NumPy and trimesh dependencies installed. The API integration
test uploads a shared-prototype STL bundle and executes the real converter subprocess. It verifies
download hashes, occurrence IDs, meter-scale bounds, protected downloads and failed unsupported units.

After rebuilding the Compose service, run the real OpenCascade smoke test inside its container:

```sh
docker compose exec -T step-worker python /app/tests/compose_smoke.py
```

For single-root STEP parts, the converter attempts to preserve explicit surface colors per face,
falling back to the owning solid or root color. The smoke test generates a colored STEP solid with
a differently colored face and checks the resulting GLB. Multi-root and component-bundle color
mapping are separate paths and are not covered by this single-part color support.

That test checks ordinary STEP metadata outputs and a shared-STEP assembly with negative/rotated
placements, native edge metadata, exact occurrence IDs and meter-scale world transforms/bounds.
It uses the container's existing worker credential and a generated box; it does not edit source CAD.
