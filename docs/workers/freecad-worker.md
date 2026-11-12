# FreeCAD worker

## Role

The FreeCAD worker is Modelryx's native CAD processing service for FreeCAD models. It accepts `.FCStd` files and assemblies, performs native CAD extraction with rich assembly semantics, and publishes validated geometry and metadata artifacts.

**Deployment flexibility:** The FreeCAD worker can run either as a **separate standalone server** (recommended for production) or **embedded inside the Modelryx server container** (for development and small-scale deployments). Both modes implement the same processing logic; the difference is operational and architectural only.

The service implementation and endpoint examples live in
[`../../workers/freecad/README.md`](../../workers/freecad/README.md).
The [worker SDK](../../worker-sdk/README.md#shared-step-and-geometry-service) describes how this
native CAD worker relates to the neutral STEP geometry service and other custom workers.

## Runtime boundary

### Standalone deployment

```text
Modelryx Server
        │ authenticated job submission
        ▼
FreeCAD Standalone Worker (:8090)
        │ durable asynchronous job
        ▼
preprocessing → native extraction → assembly analysis → GLB/geometry conversion → outputs
```

The standalone worker implements the read-only part and assembly profile in the worker protocol. It uses its own job volume and does not require Modelryx server database, object storage, or STEP service. It runs one conversion at a time, admits up to four active or queued jobs, and retains terminal job records and successful outputs for 24 hours.

### Embedded deployment

The embedded worker claims only `FCSTD_TO_MODELRYX` jobs from the shared Modelryx `ProcessingJob` queue. It shares the server's PostgreSQL and object storage but runs as a dedicated `freecad` processing role, separate from the lightweight `general-worker` that handles STEP, staging, and other job types.

## Inputs and outputs

### Primary inputs

- **Single `.FCStd` files** — Read-only document files in FreeCAD's native format.
- **Assembly packages** — ZIP files containing root `.FCStd` and linked dependencies with normalized references.
- **Dependency specifications** — External link information and package metadata from the local preprocessor.

### Outputs

- `model.glb` — Browser-ready 3D geometry with assembly structure.
- `model.pfg` — Modelryx partition format for advanced geometry analysis.
- `metadata.json` — Normalized PartMetadata v1 with assembly semantics, BOM, and property extraction.
- `assembly-evidence.json` — Native FreeCAD-specific assembly information including joint definitions, occurrence mappings, and diagnostic flags.
- `conversion-log.txt` — Detailed conversion diagnostics and warnings.
- Preview image — Static screenshot for quick visual verification.

## HTTP contract (Standalone)

- `GET /ready` — service and converter readiness.
- `GET /api/v1/capabilities` — authenticated runtime capabilities.
- `POST /api/v1/jobs` — authenticated, idempotent multipart submission.
- `GET /api/v1/jobs/{id}` — durable job status.
- `POST /api/v1/jobs/{id}/cancel` — cancellation.
- Output endpoints expose GLB, PFG, metadata, assembly evidence, and logs when ready.
- `GET /metrics` — authenticated Prometheus metrics.

Jobs progress from `QUEUED` to `PROCESSING` to `READY`; terminal alternatives are `FAILED` and `CANCELLED`. Queue state survives service restarts, transient failures are retried, and completed data is removed after the configured 24-hour retention period.

### Job submission

Submission requires:
- `request` — JSON object with job metadata, source file specifications, and output directives.
- `source` — Multipart form file (`.FCStd` or `.zip` for assemblies).

Request must include `contentSHA256`, declared size, and future `expiresAt`.

```sh
curl -i http://127.0.0.1:8090/api/v1/jobs \
  -H "X-Modelryx-Worker-Key: $MODELRYX_INTERNAL_WORKER_KEY" \
  -F "request=@request.json;type=application/json" \
  -F "source=@assembly.zip;type=application/zip"
```

Submissions should include an `Idempotency-Key` so a retry returns the existing durable job rather than starting duplicate conversion.

## Deployment modes

The FreeCAD worker can be deployed in two ways: as a **separate standalone server** or **inside the Modelryx server container**. Choose based on your operational needs and scale.

### Standalone worker (Separate container / Recommended for production)

The FreeCAD worker runs as an **independent HTTP service** in its own container, communicating with the Modelryx server via authenticated network requests.

**Characteristics:**
- Separate Docker container (`Dockerfile.standalone`)
- Independent job volume and job queue
- HTTP service on port 8090
- Communicates with server via `MODELRYX_FREECAD_WORKER_URL`

**Best for:**
- Production deployments with dedicated CAD workload capacity
- Scaling FreeCAD processing independently from the server
- Resource isolation and containment
- Operational simplicity (can restart worker without affecting server)
- TLS termination and network-level access control
- High-throughput conversion pipelines

**Build and run:**

```sh
docker build -f workers/freecad/Dockerfile.standalone -t modelryx-freecad-standalone .
```

```sh
docker run --rm --name modelryx-freecad-standalone \
  --env MODELRYX_INTERNAL_WORKER_KEY \
  --mount source=modelryx-freecad-jobs,target=/data/jobs \
  --memory 8g --pids-limit 512 \
  -p 127.0.0.1:8090:8090 modelryx-freecad-standalone
```

On a dedicated host, put the loopback listener behind a TLS reverse proxy reachable by the Modelryx server and restrict access to trusted clients. Set a request timeout and disk quota appropriate for CAD uploads and retained outputs.

### Embedded worker (Inside Modelryx server container)

The FreeCAD worker runs as a **dedicated processing role within the same server container**, claiming `FCSTD_TO_MODELRYX` jobs from the shared PostgreSQL `ProcessingJob` queue.

**Characteristics:**
- Embedded in the server container (no separate `MODELRYX_FREECAD_WORKER_URL` configuration needed)
- Shares server's PostgreSQL database and object storage
- No external network communication; uses in-process job coordination
- Dedicated `freecad` processing role (separate from `general-worker`)

**Best for:**
- Development and testing
- Single-container deployments
- Small-scale or proof-of-concept deployments
- Simplified operational complexity (single container to manage)
- Resource-constrained environments

**Build:**

```sh
docker build -f workers/freecad/Dockerfile -t modelryx-freecad-worker .
```

When using embedded mode, the `MODELRYX_FREECAD_WORKER_URL` environment variable should be empty or unset; the server automatically routes jobs to the embedded processor.

## Configuration and startup

### Environment variables

Common to both deployment modes:

- `MODELRYX_INTERNAL_WORKER_KEY` — Shared secret for authenticated endpoints (required).
- `MODELRYX_CONVERTER_VERSION` — Adapter version matching server expectations for cached conversion and artifact provenance. Must match server's `MODELRYX_CONVERTER_VERSION`.
- `MODELRYX_FREECAD_STAGE_TIMEOUT_SECONDS` — Optional per-stage time limit. Clock resets when converter reports new stage. Leave unset to retain only overall job timeout.

### Standalone configuration

Additional environment variables for standalone deployment:

- `MODELRYX_FREECAD_JOBS_ROOT` — Job volume mount point (default: `/data/jobs`).
- `FREECAD_WORKER_QUEUE_CAPACITY` — Max active/queued jobs (default: 4).
- `FREECAD_WORKER_RETENTION_SECONDS` — Output retention period (default: 86400 = 24h).

### Integration with Modelryx Server

The Modelryx server automatically detects and routes FreeCAD conversion jobs based on your deployment choice:

**For standalone deployment:**

Configure the server to forward jobs to the separate FreeCAD worker service:

1. **Environment variable** (at server startup):
   ```sh
   MODELRYX_FREECAD_WORKER_URL=https://freecad-worker.internal:8090
   MODELRYX_INTERNAL_WORKER_KEY=<shared-key>
   ```

2. **Web UI** (override at runtime) — Navigate to **Administrator → Operations → CAD server settings** to configure or verify the worker URL and readiness. Saved settings override environment defaults.

The server forwards all `FCSTD_TO_MODELRYX` jobs to the configured URL using the shared internal worker key.

**For embedded deployment:**

Leave `MODELRYX_FREECAD_WORKER_URL` unset or empty. The server automatically uses the in-process FreeCAD worker running within the same container.

**Runtime considerations:**
- Job claims are atomic; once claimed, a job stays with its assigned processor
- General worker picks up URL changes on its next polling cycle
- Availability cards check both standalone readiness (via HTTP health endpoint) and embedded worker status

### Root-document dependency packaging

The local FreeCAD preprocessor (`workers/freecad/preprocessor/modelryx_freecad_preprocessor.py`) prepares assembly packages:

- Opens `.FCStd` files read-only and classifies them by content.
- Traverses native external links and packages only reachable dependencies.
- Maps external links to normalized `externalN` references.
- Rebinds links within the package to copied documents.
- Reports missing dependencies with fix suggestions.

Invoked by the local CAD selection service through FreeCAD's bundled Python runtime.

Configure on Windows when automatic discovery fails:

```powershell
$env:MODELRYX_FREECAD_CMD = "C:\Program Files\FreeCAD 1.1\bin\python.exe"
$env:MODELRYX_FREECAD_PREPROCESSOR = "D:\project1\Modelryx\workers\freecad\preprocessor\modelryx_freecad_preprocessor.py"
```

The production FreeCAD worker does not perform dependency discovery; it converts the isolated package received from Modelryx. Preprocessing is performed only on the local agent side.

## Assembly semantics

### Native joint resolution

Native joint references capture assembly constraints with evidence of resolution:

- `resolved` — carries exact occurrence ID.
- `ambiguous` — carries candidate IDs (limited to 100; truncated results retain total count).
- `unresolved` — preserves original reference when resolution fails.

Resolution uses package-qualified document identities and explicit nested object paths. Structural occurrences are distinguished from navigation copies. Repeated definitions are never resolved by choosing the first match.

Joint `degrees_of_freedom` describes nominal joint type per `degrees_of_freedom_basis: nominal_joint_type`. It is not a solver measurement; `solver_status` remains unavailable until native solver observation is inspected. The existing document recompute can invoke FreeCAD's solver and change in-memory placements; extraction does not save those changes back to the source document. Built-in assemblies emit `ASSEMBLY_SOLVER_OBSERVATION_UNAVAILABLE` with the assembly object name.

### Link arrays and BOM

Link arrays include native definition evidence for `LinkCopyOnChange` and, for expanded arrays, each element's linked definition. Array BOM occurrences require a verified uniform definition.

An expanded array with an owned copy can be counted when:
- Native ownership and configuration selectors match
- Element targets, transforms, visibility, and suppression align completely

Other configured, heterogeneous, or unreadable definitions remain unresolved. Collapsed arrays retain element transforms and visibility but suppress per-element information.

`LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN` identifies arrays with unknown elements; downstream consumers should check this diagnostic before relying on array quantities.

### Geometry and mass properties

When array element geometry can be verified, unique face mappings establish accurate measurements. Ambiguous, failed, or larger mappings are reported as partial. When array element geometry cannot be verified, aggregate mass and center of mass are omitted instead of being derived from potentially inaccurate combined shapes.

FreeCAD's combined array shape can include suppressed elements; the worker builds visible geometry from unsuppressed elements only for arrays with complete suppression and visibility evidence.

## Reliability and security

### Bounds

- Source size limits prevent excessive memory and disk use.
- Output size limits ensure timely validation and transmission.
- Queue depth limits prevent unbounded job accumulation.
- Per-stage and overall execution timeouts prevent hung conversions.
- Retry limits and delay backoff contain transient failures.
- Output retention limits reclaim disk space after job completion.

### Validation

- Conversion outputs are validated before job status changes to `READY`.
- Geometry integrity and metadata schema compliance are checked.
- Native assembly evidence is retained even when uncertainties are detected.

### Authentication and access control

- All authenticated endpoints require the shared `MODELRYX_INTERNAL_WORKER_KEY` header.
- Unauthenticated routes (`/ready`, `/health`) do not expose job data or artifacts.
- For standalone deployment, deploy behind a TLS reverse proxy and restrict access to trusted clients.

## Runtime environment

The FreeCAD worker runs on FreeCAD 1.1.3 by default. The manually dispatched FreeCAD compatibility workflow tests previous and newer candidate runtimes against golden, dependency, and production fixture suites.

Key directories in the worker project:

- `converter/` — production FCStd conversion, metadata normalization, and controlled CAD execution.
- `preprocessor/` — local-only root selection, dependency discovery, rebinding, and packaging.
- `shared/` — low-level FCStd provenance and assembly-model inspection used by both preprocessing and conversion.
- `tools/` — optional check-in and shared STEP-service command-line utilities.

## Integration with the general pipeline

The Modelryx general processing pipeline routes:
- `.FCStd` and assembly uploads → `FCSTD_TO_MODELRYX` jobs → FreeCAD worker.
- FreeCAD worker outputs (GLB, metadata) → STEP worker (if geometry refinement needed).
- Final publication → Modelryx server and object storage.

The server's availability cards check worker readiness and authenticated capabilities using the configured internal worker key. CAD sessions currently use embedded processing; administrators can configure standalone URLs for managed publication workflows only.

## Verification

To verify a standalone deployment:

1. **Check service readiness:**
   ```sh
   curl http://127.0.0.1:8090/ready
   ```

2. **Verify authenticated capabilities:**
   ```sh
   curl -H "X-Modelryx-Worker-Key: $MODELRYX_INTERNAL_WORKER_KEY" \
     http://127.0.0.1:8090/api/v1/capabilities
   ```

3. **Test with a sample file:**
   ```sh
   curl -i http://127.0.0.1:8090/api/v1/jobs \
     -H "X-Modelryx-Worker-Key: $MODELRYX_INTERNAL_WORKER_KEY" \
     -H "Idempotency-Key: test-$(date +%s)" \
     -F "request=@request.json;type=application/json" \
     -F "source=@test-model.FCStd;type=application/octet-stream"
   ```

4. **Monitor metrics:**
   ```sh
   curl -H "X-Modelryx-Worker-Key: $MODELRYX_INTERNAL_WORKER_KEY" \
     http://127.0.0.1:8090/metrics
   ```

## Testing

The opt-in server test `server/tests/test_freecad_remote_live.py` exercises managed publication using a real standalone worker. Generate linked-assembly fixtures with `workers/freecad/tests/generate_remote_fixture.py` in a FreeCAD runtime.

Run the test against the isolated Modelryx test database:

```sh
MODELRYX_TEST_FREECAD_WORKER_URL=https://freecad-worker:8090 \
MODELRYX_TEST_FREECAD_FIXTURES=/path/to/fixtures \
MODELRYX_INTERNAL_WORKER_KEY=<key> \
python -m pytest server/tests/test_freecad_remote_live.py
```

This validates preview generation, published artifacts, and committed child revision references.

## See also

- [Worker SDK](../../worker-sdk/README.md) — protocol specification and contract.
- [New CAD Worker Guide](./new-cad-worker-guide.md) — guidance for implementing custom workers.
- [STEP Worker](./step-worker.md) — neutral geometry service for refinement and fallback.
- [Platform Architecture](../architecture/platform-architecture.md) — CAD processing pipeline and worker dispatch.
- [CAD Processing Framework](../architecture/cad-processing-framework.md) — worker coordination and embedded adapter.
- [PartMetadata v1](../architecture/partmetadata-v1.md) — metadata schema extracted by workers.
