# STEP and geometry worker

## Role

The STEP worker is Modelryx's shared neutral geometry-processing service. It accepts STEP models
and supported component-mesh bundles, performs bounded asynchronous conversion, and publishes
validated browser-ready artifacts. It is used by the general processing pipeline and by custom CAD
workers that need a neutral GLB conversion boundary.

The service implementation and endpoint examples live in
[`../../workers/step/README.md`](../../workers/step/README.md).
The [worker SDK](../../worker-sdk/README.md#shared-step-and-geometry-service) describes how this
geometry service relates to standalone native CAD workers.

## Runtime boundary

```text
Modelryx / CAD worker
        │ authenticated source submission
        ▼
STEP worker (:8080)
        │ durable asynchronous job
        ▼
preprocess → geometry conversion → metadata/preview → validation → outputs
```

This service does not own Items, Revisions, native CAD extraction, or final publication. Its outputs
are derived evidence that the caller validates and associates with authoritative source content.

## Inputs and outputs

The primary input is a STEP/STP document. Outputs include:

- `model.glb`;
- normalized metadata;
- raw converter metadata;
- structure-preview data;
- conversion diagnostics and warnings.

## HTTP contract

- `GET /health` — process liveness.
- `GET /ready` — converter and storage readiness.
- `GET /capabilities` — authenticated runtime capabilities.
- `POST /api/v1/jobs` — authenticated, idempotent submission.
- `GET /api/v1/jobs/{id}` — durable job status.
- `POST /api/v1/jobs/{id}/cancel` — cancellation.
- Output endpoints expose GLB, metadata, preview, and raw metadata when ready.
- `GET /metrics` exposes authenticated Prometheus metrics.

Jobs normally progress from `QUEUED` to `PROCESSING` to `READY`; terminal alternatives are
`FAILED` and `CANCELLED`. Queue state survives service restarts, transient failures are retried, and
completed data is removed after the configured retention period.

## Configuration and startup

The root [`compose.yaml`](../../compose.yaml) runs the service on port `8080`. Core settings include:

- `MODELRYX_INTERNAL_WORKER_KEY`
- `STEP_WORKER_DATA_ROOT`
- `STEP_WORKER_TIMEOUT_SECONDS`
- `STEP_WORKER_MAX_INPUT_BYTES`
- `STEP_WORKER_MAX_OUTPUT_BYTES`
- `STEP_WORKER_CONCURRENCY`
- `STEP_WORKER_QUEUE_CAPACITY`
- `STEP_WORKER_MAX_ATTEMPTS`
- `STEP_WORKER_RETRY_DELAY_SECONDS`
- `STEP_WORKER_RETENTION_SECONDS`

Callers must use the same shared worker key. Submissions should include an `Idempotency-Key` so a
retry returns the existing durable job rather than starting duplicate conversion.

## Reliability and security

The service bounds source/output sizes, queue depth, execution time, retries, and retention. It
validates conversion outputs before marking a job ready and does not expose unauthenticated job,
artifact, capability, or metrics routes.

## Verification

API contract, authentication, idempotency, queue saturation, cancellation, restart recovery,
retention, conversion, and output validation tests live under `workers/step/tests`. Use the
repository [testing guide](../operations/testing.md) for the full platform qualification sequence.

## Related documents

- [CAD processing framework](../architecture/cad-processing-framework.md)

