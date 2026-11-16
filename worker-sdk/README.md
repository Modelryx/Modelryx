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

## External registration and routing

Starting a worker makes its HTTP service available; it does not register it or change Modelryx
routing. In **Administrator → External CAD Workers**, register the service URL and enable its
worker ID. Modelryx uses the shared internal worker key to fetch and cache capabilities. Register
again after changing advertised formats, roles, document kinds or dependency analysis support.
The worker ID belongs to the registration; `cadSystem` identifies the native CAD system.

For generic registration, advertise at least `native-snapshot`, `part-metadata` and `model-preview`.
Modelryx requires these roles on conversion jobs and prefers `glb` when advertised. Other optional
roles must be supported by the server's publication profile before it can consume them. Advertising
an arbitrary role alone does not add a new server publication behavior.

For example, FreeCAD advertises:

```json
{
  "contractVersion": "1.0",
  "cadSystem": "freecad",
  "workerName": "FreeCAD",
  "workerVersion": "1.0.0",
  "documentKinds": ["part", "assembly"],
  "sourceExtensions": [".fcstd"],
  "outputRoles": ["native-snapshot", "part-metadata", "model-preview", "glb", "modelryx-preview"],
  "dependencyAnalysis": true
}
```

The version above is illustrative; report the actual worker release. A registered worker is
matched by enabled state, source extension and CAD system. Unknown CAD systems may be matched by
extension. More than one match is an error; neither registration order nor readiness selects a
winner. Format discovery sent to the browser and Local CAD Agent contains worker identity
and capabilities, not endpoint URLs or credentials. Administrators manage URLs separately.

### FreeCAD embedded and external coexistence

| Configuration for new `.FCStd` processing | Selected path |
| --- | --- |
| One enabled matching external registration | Generic external worker job |
| No matching external registration, dedicated FreeCAD URL configured | Existing remote PFG profile |
| No matching registration or dedicated URL | Existing embedded FreeCAD processing role |

Embedded and external FreeCAD can run simultaneously. Upload/commit, regeneration and copied
revision processing honor the external registration. Existing jobs retain their assigned job type;
starting a service does not migrate or duplicate their work. Disabling a registration affects
subsequent routing, not conversion already executing remotely. A queued generic job still needs
its matching enabled registration when it is dispatched.

An unavailable or failed selected external worker is reported through normal job failure/retry
handling; Modelryx does not automatically retry it using embedded FreeCAD. Disable or remove the
registration before submitting new work to restore the existing path. FreeCAD CAD sessions keep
using the embedded runtime. STEP/STP processing retains its built-in path.

The in-repository [FreeCAD worker](../workers/freecad/README.md#reference-external-worker) is the
reference implementation. External workers follow the same registration, multipart submission,
manifest and separate local-preprocessor boundaries; vendor runtimes, supported formats and
optional output roles remain specific to each worker.

## Endpoints

| Method and path | Behavior |
| --- | --- |
| `GET /health` | Liveness. |
| `GET /ready` | Success when the worker can accept work, including native runtime and storage checks. |
| `GET /api/v1/capabilities` | Returns `contractVersion`, `cadSystem`, `documentKinds`, `sourceExtensions` and `outputRoles`; it should also return `workerName` and `workerVersion` for display. Each of the last three required fields is a string array. Extensions include the leading dot. |
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
  "requiredOutputs": ["native-snapshot", "part-metadata", "model-preview"],
  "preferredOutputs": ["glb"],
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
[root-document source requirements](../docs/workers/new-cad-worker-guide.md#source-contract).

For managed BOM review, a worker may declare `native.transform_convention` as
`parent-row-major-mm` in PartMetadata. With that declaration, each matching
`native.occurrences` entry may provide an `id` and a 16-number row-major homogeneous
`transform`, relative to its immediate parent, with translation in millimetres and
last row `[0, 0, 0, 1]`. Its `id` must match the normalized assembly component's
`occurrence_id`. Modelryx preserves valid declared transforms when an operator resolves
an occurrence. Without a valid declaration and matrix, placement remains unavailable.

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

## Generic output roles

| Role | Media type | Contract |
| --- | --- | --- |
| `native-snapshot` | `application/json` | Native extraction evidence; preserve facts without inventing missing values |
| `part-metadata` | `application/json` | [PartMetadata v1](../docs/architecture/partmetadata-v1.md), validated against the server schema |
| `model-preview` | `application/json` | Viewer navigation with stable occurrence identity |
| `glb` | `model/gltf-binary` | Derived GLB v2 browser geometry |
| `modelryx-preview` | `application/zip` | FreeCAD PFG compatibility profile, not a substitute for `model-preview` in generic registration |

A model preview has `schemaVersion: 1`, a `rootOccurrenceId` and an `items` array. Every item has
a unique nonempty `occurrenceId`; the root must exist in the array. An optional `parentOccurrenceId`
must reference another item. Optional `renderOccurrenceIds` is a nonempty list of nonempty IDs.
Keep preview and GLB occurrence identities consistent; do not bind ambiguous geometry by name.
For example:

```json
{
  "schemaVersion": 1,
  "rootOccurrenceId": "root",
  "items": [
    {"occurrenceId": "root", "parentOccurrenceId": null, "name": "Example", "nodeType": "part"}
  ]
}
```

The server validates downloaded metadata and preview structure before publication. A `Partial`
result still includes every required output; warnings cannot replace a missing required role.
Workers may omit unsupported preferred outputs, and must not return unrequested roles.

### Current optional-output integration limit

Modelryx's generic result allowlist currently accepts `native-snapshot`, `part-metadata`,
`model-preview`, `glb` and `drawing-pdf`. Its preferred-output selection can also request
`component-geometry` for assemblies and `drawing-representations` for related drawings, but
returning those bundles currently fails generic result validation. Native worker export support
and server publication support are therefore separate qualification requirements. The dedicated
FreeCAD PFG path has its own publication behavior; this limitation does not redefine its archive.

## Local preprocessor contract

Local preprocessing and remote conversion are separate installations. The preprocessor discovers
references and creates an isolated native package on the user's machine; it does not create
Modelryx Items, publish artifacts or need the remote worker key. Native runtimes may be required
on both machines, independently. HTTP `/analyze` below is a separate optional server-side capability.

Install the Local CAD Agent, then call its `app/register-preprocessor.ps1` with `WorkerId`,
`CadSystem`, `Version`, `Extensions`, `SourceDirectory` and `Executable`. Copy the complete
executable distribution, not just the apphost. The registered worker ID must match administration.
The dialog groups advertised extensions by CAD system and retains the selected system filter,
so multiple workers can advertise the same extension without selecting an arbitrary preprocessor.
A matching local registration is required
when an external worker is selected. FreeCAD's built-in local path remains available without one.

The agent starts the executable with exactly two arguments:

```text
preprocessor <absolute-source-path> <output-directory>
```

Return exit code zero on success and nonzero on execution failure. Send diagnostics to stderr,
and emit the JSON dependency report as one complete stdout line. Create the declared package
inside the assigned output directory. If `package` is omitted, the agent expects `cad-package.zip`.
For a successfully isolated single-file example:

```json
{
  "contract_version": "1.0",
  "analyzer_version": "example-dependencies-1",
  "document_type": "PART",
  "root_file": "Part.cad",
  "root_logical_path": "Part.cad",
  "files": [{"logical_path": "Part.cad", "is_root": true, "document_type": "PART"}],
  "references": [],
  "missing_references": [],
  "isolated_reopen_validated": true
}
```

Use package-relative logical paths and exactly one root. Include each dependency and its referring
document; report missing references with `reference`, `referenced_by` and a `kind`, optionally a
repair `suggestion`. Missing references block acceptance until repaired. Rebase only package copies,
then reopen them without allowing the original source locations to satisfy references. Set
`isolated_reopen_validated` only after this succeeds. The agent validates the report against the
package and enforces its own file, path and size limits.

For local duplicate checking, optionally write normalized `part-metadata.json` in the output
directory. The agent adds it to the report with `metadata_source_sha256` for the selected source;
extract it from the validated package while preserving the original source identity. Keep native
extraction and conversion behind disposable process boundaries and propagate cancellation to
owned child processes.

FreeCAD provides [build and registration scripts](../workers/freecad/README.md#local-preprocessor-registration)
and an executable host as an example. The generic agent has no vendor SDK dependency.

## FreeCAD conversion profile

FreeCAD advertises `freecad`, part and assembly document kinds, and the `.fcstd` extension.
It returns its existing PFG preview archive as `modelryx-preview` (`application/zip`), normalized
`part-metadata` (`application/json`), and raw `native-snapshot` (`application/json`). It also
exposes `model-preview` (`application/json`) and `glb` (`model/gltf-binary`) for generic external
registration. An enabled matching FreeCAD registration takes precedence; without one, embedded
and the existing dedicated standalone-URL processing remain available. The dedicated URL
integration retains its PFG publication path; external registration uses generic CAD publication
with separately validated GLB, navigation, metadata and native extraction outputs. The original FCStd package remains authoritative.
Dependency discovery and CAD sessions have separate lifecycles and are outside this conversion profile.

## Optional HTTP dependency analysis

External workers may advertise `dependencyAnalysis: true` and implement the authenticated
`POST /analyze` endpoint. It accepts an `application/zip` body and the UTF-8 root path encoded
in `X-Modelryx-Root-Path-B64`. It returns a JSON dependency report without publishing records:

```json
{
  "document_type": "ASSEMBLY",
  "root_file": "root.cad",
  "root_logical_path": "project/root.cad",
  "files": [
    {"logical_path": "project/root.cad", "is_root": true, "document_type": "ASSEMBLY"},
    {"logical_path": "project/part.cad", "is_root": false, "document_type": "PART"}
  ],
  "references": [{"parent_logical_path": "project/root.cad", "child_logical_path": "project/part.cad", "instance_name": "part:1"}],
  "missing_references": []
}
```

List every referenced document and occurrence, use stable unique occurrence names within each
parent, and report missing references explicitly. Reference entries may also include `transform`,
`is_visible`, `is_suppressed`, `source_reference`, `reference_designator`, and `properties`.
The operator reviews the report and resolves Item identities before Modelryx accepts a replacement.
Workers must apply the same ZIP path and resource limits as for job submissions. Re-register after
changing advertised capabilities.

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
  --output model-preview=application/json --output glb=model/gltf-binary \
  --output part-metadata=application/json --output native-snapshot=application/json
```

Set `MODELRYX_INTERNAL_WORKER_KEY` in the environment. For a dependency ZIP, pass
`--media-type application/zip` and the root's path within the archive. The probe creates a real
worker job and uses the worker's normal retention policy. Active cancellation, restart recovery,
TLS deployment, and CAD-specific semantic outputs need separate qualification.

To qualify the existing dedicated FreeCAD URL/PFG integration separately, replace the
`model-preview` and `glb` probe roles with `--output modelryx-preview=application/zip`.
Passing the generic probe does not establish native metadata accuracy, BOM fidelity or local
preprocessor correctness. Exercise parts, repeated/nested assembly references, missing references,
source immutability and embedded fallback using the reference worker's tests and native fixtures.
