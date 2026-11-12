# Designing a new CAD worker

This guide explains how to create a custom CAD worker for Modelryx and integrate it into the processing pipeline.

## Overview

A CAD worker is an independently deployable HTTP service that:

1. Accepts authenticated job submissions from Modelryx
2. Processes CAD files asynchronously
3. Extracts and normalizes metadata
4. Produces browser-ready geometry (GLB format)
5. Validates and publishes outputs to object storage
6. Reports job status and results back to the server

Workers are **not** part of the main Modelryx server; they are separate services that run in isolated environments with their own lifecycle, scaling, and failure isolation. Deployment method is flexible—use Docker, native executables, Python scripts, or any approach suitable for your environment—as long as the HTTP API contract is satisfied.

## Goals and ownership

A worker must preserve the native file as authoritative, retain loss-minimized extraction evidence, publish valid `PartMetadata` v1, create a browser representation, preserve assembly structure, and remain testable without a CAD license.

The worker is an adapter. Modelryx owns Item/Revision identity, immutable content snapshots, authorization, lifecycle, BOM persistence, comparison, where-used, search, and UI behavior.

## Architecture model

```text
Modelryx Server
        │
        │ authenticated job submission
        │ (item ID, revision ID, file location)
        │
        v
┌─────────────────────────────────────────┐
│     Your Custom CAD Worker Service      │
│                                         │
│  • Fetch source CAD file                │
│  • Extract native metadata              │
│  • Convert to PartMetadata v1           │
│  • Generate GLB geometry                │
│  • Validate outputs                     │
│                                         │
└──────────────┬──────────────────────────┘
               │
               │ publish to object storage
               │
               v
         [Artifacts stored]
               │
               │ report status + result URLs
               │
               v
        Modelryx Server
        (publishes revision)
```

## Required boundaries

Use four boundaries even if the implementation language differs:

1. **Native adapter** — the only layer importing the CAD SDK or automating the application. It opens
   documents read-only, extracts native facts, and exports geometry or drawings.
2. **Worker core** — SDK-free snapshot models, normalization, validation, deterministic staging,
   and recorded-fixture tests.
3. **Worker service** — exposes the versioned capability and job API, reconstructs packages, invokes
   the adapter as a child process, calls converters, publishes role-based outputs, and manages
   bounded workspaces.
4. **Server integration** — capability routing, job envelopes, secure transfer, schema validation,
   Artifact lineage, immutable publication, and generic BOM/component publication.

Do not load a vendor SDK into Modelryx or the long-running worker API process. Vendor process crashes, hangs, or license issues should not affect the server. A disposable adapter process contains that failure.

## Job lifecycle

Jobs flow through these states:

- **QUEUED** — Waiting in worker queue
- **PROCESSING** — Actively being worked on
- **READY** — Successfully completed, outputs available
- **FAILED** — Processing failed; diagnostics reported
- **CANCELLED** — User or system cancelled the job

## Source contract

Declare supported extensions and document kinds explicitly. Distinguish parts, assemblies,
drawings, presentations, and supporting files. When extensions overlap another worker, such as
DWG (AutoCAD & Inventor), advertise capabilities and define deterministic fallback.

Assemblies should use the root-document contract: one declared root, safe relative
paths, a bounded dependency graph, and explicit missing-reference diagnostics. Never incorporate a
temporary worker path into component identity.

The native source remains authoritative. STEP, GLB, component meshes, PDF, metadata, and BOM views
are derived outputs with source-snapshot and generator provenance.

## Required outputs

Your worker must produce (at minimum):

1. **model.glb** — Browser-ready 3D geometry in GLB format
   - Optimized for web viewing (meshes, materials, bounds)
   - Must include bounding-box metadata
   - Use Three.js-compatible encoding

2. **metadata.json** — Normalized `PartMetadata` v1 document
   - Schema version `"1.0"`
   - Must validate against [PartMetadata v1 specification](../architecture/partmetadata-v1.md)
   - Include all extractable features: extrusions, holes, pockets, fillets, threads, materials, mass, etc.
   - Unknown or unsupported fields must be left empty or null (never inferred)

3. **preview.json** — Structure-tree preview data
   - Hierarchical assembly/part tree
   - Bounding boxes for each component
   - Occurrence IDs for 3D viewer selection mapping

4. **diagnostics.txt** — Optional conversion warnings and debug info
   - Tool/version information
   - Non-fatal warnings (missing materials, unsupported features, etc.)
   - Processing time and resource usage

## Output contract

Every successful extraction should expose semantic output roles for:

- a loss-minimized native extraction snapshot;
- normalized metadata validated against the authoritative Modelryx schema;
- geometry or drawing output appropriate to the document;
- warnings for incomplete optional extraction.

Prefer STEP for reliable solid translation, a component geometry bundle when whole-assembly STEP
loses names or hierarchy, and PDF for drawings. Direct GLB output is acceptable when its lineage is
recorded. A preview is never authoritative engineering content.

## Native snapshot design

Define an SDK-free snapshot model before the normalizer. Represent native facts, not the final JSON
shape. Capture, where available:

- document kind, application/version, units, configuration/model state, and properties;
- part number, revision, description, material, parameters, and native features;
- mass, density, volume, surface area, center, and bounds with source units;
- drawing sheets, order, size, views, title blocks, dimensions, and annotations;
- warnings at the narrowest field or collection that failed.

For every assembly occurrence retain:

- stable native occurrence ID and separate definition ID;
- parent occurrence ID and ordered hierarchy;
- package-relative definition path;
- reference designator/display name;
- per-parent quantity and documented 4×4 transform convention;
- suppression, visibility, exclusion/reference/phantom/substitute semantics;
- configuration identity such as model state or family member;
- component part number/revision and optional component properties.

Treat SDK collections and children as nullable even when vendor documentation says otherwise.
Missing optional data should become a warning or empty collection, never a null-reference crash.

## PartMetadata normalization

Normalization must be deterministic and SDK-free. Validate against the authoritative schema in the server codebase; do not keep a private schema copy that can drift.

- Convert dimensions to millimetres and physical values to schema units.
- Distinguish `complete`, `partial`, `unavailable`, and `not_applicable` honestly.
- Omit unknown values instead of writing zero or guesses.
- Retain native-only evidence in the raw snapshot or `native` section.
- Validate before upload and again on the server.
- Record worker and contract versions.

The server schema is strict. End-to-end qualification must use a freshly built API image; a stale
runtime can reject fields that current source and tests accept.

## Geometry and preview strategy

- **Part:** native STEP to the shared converter is the preferred default.
- **Assembly:** use component STEP/mesh entries and occurrence transforms when monolithic export
  flattens structure or makes selection ambiguous.
- **Drawing:** publish PDF plus normalized sheet/entity metadata.
- **No geometry:** allow metadata-only success only for document kinds whose contract permits it.

Never bind a mesh on a weak name-only match. Publishing `previewAvailable: false` is safer than
highlighting the wrong component. Test repeated definitions, root-space transforms, negative
coordinates, rotation, nested assemblies, and scaling policy.

## Generic BOM publication

CAD extraction ends at occurrence/native-BOM evidence. Server publication must create generic
BOM records. This automatically enables Structure, recursive comparison,
where-used, search, export, and viewer behavior.

Identity priority is:

1. durable native occurrence ID;
2. stable package-relative definition plus configuration identity;
3. component Item identity or part number;
4. bounded heuristic matching with explicit confidence.

Do not use temporary paths, list positions, or display names as sole durable identity. Repeated
placements share a definition but remain distinct occurrences. Quantities are per parent;
Modelryx computes recursive totals. Publish nested subassembly BOMs on their own generated
revisions rather than flattening everything into the root.

## HTTP contract

Your worker must implement these authenticated endpoints:

### Health and readiness

```
GET /health
  Response: 200 OK
  Body: { "status": "healthy" }

GET /ready
  Response: 200 OK when ready to accept jobs
  Response: 503 Service Unavailable during startup/maintenance
```

### Job submission

```
POST /api/v1/jobs
  Headers:
    Authorization: Bearer {MODELRYX_INTERNAL_WORKER_KEY}
    Idempotency-Key: {unique-uuid}  # Optional but recommended
  
  Body:
    {
      "source_uri": "s3://bucket/path/model.step",
      "source_sha256": "abc123...",
      "source_content_type": "application/step",
      "item_id": "item-uuid",
      "revision_id": "revision-uuid",
      "worker_version": "1.0",
      "timeout_seconds": 300,
      "context": {
        "material_library_url": "https://server/api/v1/materials",
        "standards": ["ASME", "ISO"]
      }
    }
  
  Response: 201 Created
  Body:
    {
      "job_id": "job-uuid",
      "status": "QUEUED",
      "created_at": "2026-11-11T15:30:00Z",
      "expires_at": "2026-11-12T15:30:00Z"
    }
```

### Job status and polling

```
GET /api/v1/jobs/{job_id}
  Headers:
    Authorization: Bearer {MODELRYX_INTERNAL_WORKER_KEY}
  
  Response: 200 OK
  Body:
    {
      "job_id": "job-uuid",
      "status": "PROCESSING",
      "progress_percent": 45,
      "created_at": "2026-11-11T15:30:00Z",
      "started_at": "2026-11-11T15:30:15Z",
      "completed_at": null,
      "result": null
    }
```

When status is `READY`:

```
{
  "job_id": "job-uuid",
  "status": "READY",
  "result": {
    "model_url": "s3://bucket/output/model.glb",
    "metadata_url": "s3://bucket/output/metadata.json",
    "preview_url": "s3://bucket/output/preview.json",
    "diagnostics_url": "s3://bucket/output/diagnostics.txt",
    "processing_time_seconds": 42,
    "worker_version": "1.0"
  }
}
```

When status is `FAILED`:

```
{
  "job_id": "job-uuid",
  "status": "FAILED",
  "error": {
    "code": "UNSUPPORTED_FORMAT",
    "message": "File format not supported",
    "details": "Expected STEP; received IGES"
  }
}
```

### Job cancellation

```
POST /api/v1/jobs/{job_id}/cancel
  Headers:
    Authorization: Bearer {MODELRYX_INTERNAL_WORKER_KEY}
  
  Response: 200 OK
  Body: { "status": "CANCELLED" }
```

## Authentication and security

- Workers use a shared internal key: `MODELRYX_INTERNAL_WORKER_KEY`
- All submissions from Modelryx include this key in the `Authorization: Bearer` header
- Your worker must validate this key on every authenticated request
- Submissions should include an `Idempotency-Key` so retries return the existing job rather than starting duplicates
- Worker credentials are **never** passed to the browser or exposed in client-side code
- Validate output file sizes and content types before marking a job `READY`

## Configuration and deployment

### Environment variables your worker should support

```dotenv
# Core Modelryx integration
MODELRYX_INTERNAL_WORKER_KEY=shared-secret-key
WORKER_LISTEN_HOST=0.0.0.0
WORKER_LISTEN_PORT=8000

# Object storage for outputs
WORKER_STORAGE_TYPE=s3  # or local, gcs, azure
WORKER_S3_BUCKET=modelryx-artifacts
WORKER_S3_REGION=us-east-1
WORKER_S3_PREFIX=cad-worker-output/

# Processing constraints
WORKER_TIMEOUT_SECONDS=300
WORKER_MAX_INPUT_BYTES=500000000  # 500 MB
WORKER_MAX_OUTPUT_BYTES=100000000  # 100 MB
WORKER_CONCURRENCY=4              # Max parallel jobs
WORKER_QUEUE_CAPACITY=100         # Max queued jobs

# Logging and monitoring
WORKER_LOG_LEVEL=INFO
WORKER_METRICS_PORT=9090
```

### Deployment options

Workers can be deployed using any approach that provides the HTTP API contract. Common deployment patterns include:

**Docker container** (recommended for portability and isolation):

```dockerfile
FROM ubuntu:22.04

# Install your CAD processing dependencies
RUN apt-get install -y ... 

# Copy worker code
COPY . /app
WORKDIR /app

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:${WORKER_LISTEN_PORT}/health || exit 1

EXPOSE 8000 9090
CMD ["python", "worker.py"]
```

**Native executable** (C#, C++, Go, etc.):
- Compile your worker to a .exe (Windows) or binary (Linux/macOS)
- Install on target server with required dependencies
- Run as a service or daemon
- Environment variables are passed to the process

**Direct Python/Node.js** (development or embedded):
- Run worker script directly on server
- Manage via systemd, supervisor, or other process manager
- No containerization overhead

**Standalone service**:
- Deploy on licensed hardware (e.g., Windows host for AutoCAD-based processing)
- Start via automation or manual configuration
- Must be reachable via configured URL and shared worker key

The Modelryx server discovers and routes to your worker based on configured URLs and capabilities, regardless of deployment method. Docker is optional; any HTTP service implementing the contract works.

## Example: Minimal Python worker

Here's a starter template:

```python
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
import uuid
import asyncio
from datetime import datetime, timedelta

app = FastAPI()

# In-memory job store (use database for production)
jobs = {}

INTERNAL_KEY = "your-shared-key"

def verify_auth(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Missing authorization")
    token = authorization[7:]
    if token != INTERNAL_KEY:
        raise HTTPException(status_code=403, detail="Invalid authorization")

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/ready")
async def ready():
    # Return 503 if dependencies are not ready
    return {"status": "ready"}

@app.post("/api/v1/jobs")
async def submit_job(
    body: dict,
    authorization: str = Header(None)
):
    verify_auth(authorization)
    
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "status": "QUEUED",
        "result": None,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "expires_at": (datetime.utcnow() + timedelta(hours=24)).isoformat() + "Z"
    }
    
    jobs[job_id] = job
    
    # Queue async processing
    asyncio.create_task(process_job(job_id, body))
    
    return JSONResponse(status_code=201, content=job)

@app.get("/api/v1/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    authorization: str = Header(None)
):
    verify_auth(authorization)
    
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return jobs[job_id]

async def process_job(job_id: str, submission: dict):
    """Background job processing"""
    jobs[job_id]["status"] = "PROCESSING"
    
    try:
        # 1. Download source CAD file
        source_uri = submission["source_uri"]
        # ... fetch from S3/object storage
        
        # 2. Process CAD file
        # ... extract metadata, convert to GLB
        
        # 3. Upload outputs to object storage
        # ... upload model.glb, metadata.json, preview.json
        
        # 4. Mark job complete
        jobs[job_id]["status"] = "READY"
        jobs[job_id]["result"] = {
            "model_url": "s3://bucket/output/model.glb",
            "metadata_url": "s3://bucket/output/metadata.json",
            "preview_url": "s3://bucket/output/preview.json",
            "processing_time_seconds": 42
        }
    except Exception as e:
        jobs[job_id]["status"] = "FAILED"
        jobs[job_id]["error"] = {
            "code": type(e).__name__,
            "message": str(e)
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

## Integration with Modelryx

Once your worker is deployed:

1. **Register with server**: Add worker configuration to Modelryx server environment:
   ```
   MODELRYX_CAD_WORKERS=worker1,worker2,your-worker
   MODELRYX_WORKER_your_worker_BASE_URL=http://your-worker:8000
   MODELRYX_WORKER_your_worker_TIMEOUT=300
   ```

2. **Add worker capability detection**: Modelryx calls `GET /api/v1/capabilities` to discover supported file formats and features

3. **Server will automatically**:
   - Route submissions to your worker based on file type and availability
   - Poll job status asynchronously
   - Validate outputs match the expected schema
   - Publish successful results to revision
   - Log failures and retry on transient errors

## Testing your worker

1. **Unit tests**: Test metadata extraction and GLB conversion independently
2. **Integration tests**: Submit jobs via HTTP and verify outputs
3. **Load tests**: Ensure your worker handles queue depth and concurrent jobs
4. **Schema validation**: Validate all metadata.json files against PartMetadata v1 JSON schema

Run the Modelryx evaluation harness against your worker:

```bash
python -m modelryx.engineering_search.evaluation \
  --corpus tests/search/search_reliability_v1.json \
  --worker-url http://your-worker:8000 \
  --schema tests/search/search_reliability.schema.json
```

## Reliability patterns

### Idempotency
Use `Idempotency-Key` headers so retries of the same submission return the existing job:

```python
@app.post("/api/v1/jobs")
async def submit_job(
    body: dict,
    idempotency_key: str = Header(None),
    authorization: str = Header(None)
):
    verify_auth(authorization)
    
    if idempotency_key and idempotency_key in submitted_keys:
        return JSONResponse(status_code=200, content=jobs[submitted_keys[idempotency_key]])
    
    job_id = create_job(body)
    if idempotency_key:
        submitted_keys[idempotency_key] = job_id
    
    return JSONResponse(status_code=201, content=job)
```

### Timeouts and retries
- Set `WORKER_TIMEOUT_SECONDS` conservatively (300-600 seconds typical)
- Modelryx will retry failed jobs up to N times before marking them as permanently failed
- Use exponential backoff for external API calls

### Monitoring
- Export Prometheus metrics on `/metrics`:
  - `worker_jobs_total{status}` — Job count by status
  - `worker_job_duration_seconds` — Processing time histogram
  - `worker_job_size_bytes` — Input file size distribution
- Log all errors with job ID and submission details for debugging

## See also

- [STEP Worker](./step-worker.md) — Reference implementation
- [PartMetadata v1](../architecture/partmetadata-v1.md) — Metadata schema
- [CAD Processing Framework](../architecture/cad-processing-framework.md) — Pipeline architecture

## Security and safety

- Authenticate worker endpoints with `MODELRYX_INTERNAL_WORKER_KEY`.
- Reject absolute, drive-qualified, traversal, duplicate, and overlong archive paths.
- Bound package count, expanded bytes, individual size, recursion, and diagnostics.
- Open source documents read-only and restore application-global state in `finally` blocks.
- Resolve or rebind dependencies only on temporary copies, never the user's originals.
- Keep licensed applications and worker keys on controlled hosts.
- Publish immutable snapshots; never mutate a released source Artifact.

## Failure model

Report the failing stage and an actionable fix. Stages should distinguish initialization,
license/startup, package reconstruction, document open, metadata extraction, dependency resolution,
geometry export, conversion, upload, schema validation, and publication.

Classify failures as retryable infrastructure, user-correctable source/dependency, unsupported
capability, worker configuration/licensing, or partial optional representation. Successful outputs
should survive an unrelated optional drawing or component-preview failure. Never hide an incomplete
assembly behind an apparently successful flattened preview.

## Test ladder and release gates

1. **SDK-free unit tests:** validation, units, null collections, warnings, transforms, repeated
   occurrences, BOM semantics, and deterministic output.
2. **Recorded/golden fixtures:** raw snapshot, normalized metadata, manifests, and geometry. Review
   semantic diffs before replacing goldens.
3. **Server integration:** authentication, claim, idempotency, upload, failure, cancellation,
   immutable publication, generated components, nested BOM, comparison, and where-used.
4. **Generic cross-CAD contract:** run the same assertions used for other worker implementations.
5. **Native smoke tests:** real part, nested/repeated assembly, relative dependency, missing
   dependency, drawing, and malformed/unsupported source.
6. **Production qualification:** real server, converter, licensed application, valid browser
   artifact, component count, cleanup, and retry/failure behavior.

Native tests belong on a dedicated licensed host. Check in legally distributable project-owned
fixtures and goldens so CI remains useful after a trial or license expires.

## Minimum fixture set

- A self-contained part with known dimensions and physical properties.
- A nested assembly with repeated definitions, transforms, and quantities.
- A portable relative-reference package.
- A missing-reference case whose unresolved occurrences remain visible.
- A multi-sheet drawing with title-block data, when supported.
- Renamed/moved component revisions for comparison.
- Suppressed/reference/phantom or closest native equivalents.
- A large-enough assembly for timeout and memory observation.

Record the CAD application version that produced every native fixture and golden.

## Recommended implementation sequence

1. Write the capability table and unsupported cases.
2. Define snapshot models and recorded fixtures.
3. Implement normalization without the CAD SDK.
4. Add job routing and a fake-worker server test.
5. Implement the native adapter for one simple part.
6. Add conversion and end-to-end publication.
7. Add dependency packaging.
8. Add occurrences, nested BOM publication, and component previews.
9. Qualify missing references, partial success, cancellation, and cleanup.
10. Run the cross-CAD contract and native production qualification.

## Review checklist

- Can core tests run without the application or SDK?
- Is native content authoritative and every output traceable to its snapshot?
- Are raw evidence and normalized metadata retained?
- Are units and quality states explicit and schema-valid?
- Are dependency paths portable and safe?
- Are occurrence IDs stable across ordinary rename/move operations?
- Are repeated components, transforms, quantities, and subassemblies preserved?
- Do generic Structure, comparison, where-used, search, and viewer selection work unchanged?
- Do failures identify their stage and preserve a useful diagnostic workspace?
- Do native fixtures and an end-to-end standalone service submission pass on the supported version?

## Existing implementations to study

Refer to these reference implementations in the Modelryx repository:

- `workers/freecad/` — Headless conversion, direct FCStd processing, and dependency rebinding on copies.
- `workers/step/` — Shared neutral/component geometry conversion.
- `server/src/server/modelryx/metadata/` — Authoritative normalized schema and validation.

Study how these workers handle:
- Dependency discovery and safe package handling
- Native BOM and configuration metadata extraction
- Deterministic golden fixtures and test automation
- Generic BOM publication and recursive relationships
- Error handling and partial success scenarios
