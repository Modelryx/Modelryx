# Modelryx standalone CAD worker protocol

This is the draft v1 baseline for read-only native CAD conversion. The existing Modelryx remote
CAD API informed the endpoint and envelope shapes. Existing worker deployments may have
vendor-specific extensions. The protocol can be implemented in any language. The optional
`modelryx-worker-sdk` Python package provides output manifest construction and validation.
The language-neutral [OpenAPI definition](contract/openapi.yaml) describes the HTTP envelopes.
The behavior and security requirements in this document also apply to implementations.

Version 1.0 permits additive optional fields and capabilities. Existing required fields, endpoint
semantics, output role meanings, units, and identity rules stay stable within 1.0. An incompatible
change requires a new contract version and explicit server support. Workers advertise the version
they implement, and the server accepts only versions it supports.

## Deployment and ownership

Modelryx connects to a worker at an operator-configured URL and sends a snapshot of the native
source. Run the worker on a private network and use TLS for cross-host traffic. Use
`X-Modelryx-Worker-Key` on capabilities, job, and output requests. The worker owns its native
runtime, temporary files, and job retention. It needs neither Modelryx database credentials nor
object storage access. Modelryx validates results and owns engineering records and publication.

## Endpoints

| Method and path | Behavior |
| --- | --- |
| `GET /health` | Liveness. |
| `GET /ready` | Success when the worker can accept work, including native runtime and storage checks. |
| `GET /api/v1/capabilities` | Returns `contractVersion`, `cadSystem`, `documentKinds`, `sourceExtensions` and `outputRoles`. Each of the last three fields is a string array. Extensions include the leading dot. |
| `POST /api/v1/jobs` | Accepts multipart `request` (JSON) and `source` (bytes). Returns 202 with a job record and a job-scoped Location header. |
| `GET /api/v1/jobs/{jobId}` | Returns the job record, or 404 for an unknown or reclaimed job. |
| `POST /api/v1/jobs/{jobId}/cancel` | Requests cancellation and returns 202. Repeated cancellation is safe. A terminal job stays terminal. |
| `GET /api/v1/jobs/{jobId}/outputs/{role}` | Returns the declared media type and bytes after a publishable result is available. |

Invalid credentials return 401. Invalid submissions return 400. A conflicting reuse of a job ID
returns 409. Return 429 with Retry-After when at capacity.

## Submission

```json
{
  "contractVersion": "1.0",
  "jobId": "95fc8a83-24cc-409a-b121-b4b951bfbf2c",
  "attempt": 1,
  "cadSystem": "freecad",
  "documentKind": "assembly",
  "source": {
    "snapshotId": "ed42e268-91bb-48bd-8eb6-74182443c31e",
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "size": 24576,
    "rootPath": "Pump.FCStd"
  },
  "configuration": null,
  "requiredOutputs": ["modelryx-preview", "part-metadata", "native-snapshot"],
  "preferredOutputs": [],
  "expiresAt": "2030-01-01T00:00:00Z"
}
```

The job ID is a lowercase canonical UUID. `attempt` is a positive integer. `configuration` is
null unless the worker profile defines a configuration object. Use an RFC 3339 timestamp with a
timezone for `expiresAt`. Reject expired requests, unsupported document kinds or required output
roles, and source size or SHA-256 mismatches. Unsupported preferred outputs may be omitted.
Exact replays return the existing job, including after a worker restart during its retention
period. A new execution attempt uses a new ID.

The source is either a native document or a ZIP of the root and its dependencies. Use the source
multipart media type to distinguish a dependency ZIP (`application/zip`) from a native upload
(`application/octet-stream`), since the native format itself can be ZIP-based. `rootPath` is a
case-preserving, relative POSIX path. Verify it against the package, enforce input and extracted
size/count limits, and reject traversal, links, and ambiguous paths before invoking CAD. Preserve
relative references and report missing dependencies. See the
[CAD root-document contract](../docs/architecture/cad-root-document-packaging.md).

## Job records and results

Job records include `id`, `state`, `stage`, and integer `progress` (0 to 100). A job can move
through `Queued`, `Reconstructing`, `Extracting`, and `Validating` before reaching
`ReadyToPublish`, `Partial`, `Failed`, `Cancelled`, or `Expired`. `Failed` includes
`error` with `code`, `message`, `retryable`, and `stage`. Cancel or expire native work and clean
its workspace without exposing unfinished outputs.

`ReadyToPublish` and `Partial` include a `result` with the following fields.

- `contractVersion`, `jobId`, `attempt`, `cadSystem`, and `configuration` echo the request.
  `sourceSha256` matches the source bytes. `adapterVersion` identifies the adapter build and
  `cadVersion` identifies the native CAD runtime when available.
- `status` is `complete` for `ReadyToPublish`, and `partial` for `Partial`. Partial results
  include one or more strings in `warnings` and still contain every required output.
- `outputs` lists one entry per produced role requested by the server. Each entry has `role`,
  `mediaType`, `size` (positive byte count), `sha256` (64 hex characters), `downloadUrl`
  (exactly `/api/v1/jobs/{jobId}/outputs/{role}`), and `provenance` with `source_role` and
  `generator`. Role names use lowercase ASCII letters, digits, and hyphens and start with a
  letter. A successful result references finalized outputs.
- `diagnostics` can carry structured extraction evidence. Keep credentials and local paths out
  of client-visible diagnostics.

Validate identity, roles, media types, and declared sizes before downloading. Download only from
the configured worker origin without redirects. Enforce byte limits during download and check
actual sizes, hashes, schemas, and package contents before publishing to Modelryx.

## FreeCAD conversion profile

FreeCAD advertises `freecad`, part and assembly document kinds, and the `.fcstd` extension.
It returns its existing PFG preview archive as `modelryx-preview` (`application/zip`), normalized
`part-metadata` (`application/json`), and raw `native-snapshot` (`application/json`). Reuse the
existing FreeCAD publication path so preview, drawing, and assembly relationships retain their
current meaning. The original FCStd package remains authoritative. Dependency discovery and CAD
sessions have separate lifecycles and are outside this conversion profile.

Workers that advertise `dependencyAnalysis: true` may also expose the authenticated FreeCAD
`POST /analyze` endpoint. It accepts a ZIP request body and a base64-encoded root path in
`X-Modelryx-Root-Path-B64`. It returns the existing dependency-analysis report and does not
publish engineering records.

See the [standalone FreeCAD deployment](../workers/freecad/README.md#standalone-http-service)
for the reference implementation.

## Shared STEP and geometry service

The public [STEP worker](../workers/step/README.md) is a separately deployable conversion service.
Modelryx can submit STEP files directly, and CAD workers can submit exported STEP or supported
component bundles to generate GLB. Its existing capability endpoint and job states form a
geometry-service contract, while this SDK's CAD job envelope covers native extraction. Clients
should check the service's advertised capabilities and validate downloaded output before use.

For a native CAD job, retain the native snapshot as the source of record and record the STEP or
component export and GLB as derived outputs with generator and source provenance. Component-bundle
identities, hierarchy, transforms, and units must survive conversion. The server remains responsible
for associating validated results with the appropriate revision and publishing engineering records.

## SDK development

Run `python -m pytest` from this directory. To install the Python helpers locally, run
`python -m pip install ./worker-sdk` from the repository root.

The opt-in [conformance probe](conformance/check_worker.py) exercises authentication, capability
discovery, multipart submission, replay, conflicting requests, terminal status, output hashes, and
terminal cancellation through HTTP. Install `./worker-sdk[conformance]`, then run:

```sh
python worker-sdk/conformance/check_worker.py --url http://127.0.0.1:8090 \
  --source Pump.FCStd --root Pump.FCStd --cad-system freecad --document-kind part \
  --output modelryx-preview=application/zip --output part-metadata=application/json \
  --output native-snapshot=application/json
```

Set `MODELRYX_INTERNAL_WORKER_KEY` in the environment. For a dependency ZIP, pass
`--media-type application/zip` and the root's path within the archive. The probe creates a real
worker job and uses the worker's normal retention policy. Active cancellation, restart recovery,
TLS deployment, and CAD-specific semantic outputs need separate qualification.
