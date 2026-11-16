# Designing a new CAD worker

Use the [worker SDK protocol](../../worker-sdk/README.md) and
[OpenAPI contract](../../worker-sdk/contract/openapi.yaml) for exact HTTP envelopes.
The in-repository [FreeCAD worker](../../workers/freecad/README.md#reference-external-worker)
is the native external-worker reference. [STEP](../../workers/step/README.md) is a separately
deployed geometry service with its own job API, not the generic native envelope.

## Ownership and boundaries

The native source remains authoritative. Workers extract native facts and create derived
representations. Modelryx owns authorization, Item/revision identity, content snapshots,
artifact lineage, lifecycle, managed BOMs, comparison, where-used, search and publication.
Independent workers need no Modelryx database credentials or object-store mount.

Use four boundaries:

1. **Native adapter:** vendor runtime/SDK operations in a disposable child process; read native
   copies, extract facts and export geometry/drawings without saving original user documents.
2. **Core:** SDK-free snapshot models, deterministic normalization, bounded staging/validation
   and recorded-fixture tests.
3. **Worker service:** authenticated HTTP jobs, safe package reconstruction, process supervision,
   cancellation, durable state, retention and role-based result manifests.
4. **Modelryx integration:** registration, capability matching, secure transfer, schema validation,
   immutable publication and operator-resolved engineering identity.

Do not import vendor SDKs into the Modelryx web server. Stop owned native processes on timeout or
cancellation and keep queues, workspaces, native logs and diagnostics bounded. Runtime licensing
and redistribution remain worker-host responsibilities.

## Registration and selection

Register the service URL in **Administrator > External CAD Workers**. Modelryx fetches and caches
capabilities using the shared internal key. Starting a service alone does not register it;
re-register after capability changes. The worker advertises `contractVersion: "1.0"`, `cadSystem`,
name/version, document kinds, extensions and output roles. Generic registration requires
`native-snapshot`, `part-metadata` and `model-preview`.

Selection matches enabled registrations, source extension and CAD system. Multiple matches
are errors; readiness does not arbitrarily choose between registrations.A failed selected external worker is not automatically retried through another system.

FreeCAD registration overrides new FCStd conversion routing. Without a matching registration,
its dedicated standalone URL or embedded role remains available. FreeCAD CAD sessions still use
the embedded runtime. STEP/STP retains the built-in geometry-service route.

## Source contract

Submit multipart files named `request` (JSON) and `source` (bytes). The envelope declares a
canonical job UUID, positive attempt, CAD system, document kind, source snapshot UUID, exact
byte size/SHA-256, package-relative root path, required/preferred roles and future expiry.
Use `application/octet-stream` for a native file and `application/zip` for a dependency package;
native formats themselves may be ZIP-based, so do not guess from magic bytes.

A dependency package has one declared root and safe case-preserving POSIX paths. Bound member
counts, expanded bytes, path length and graph depth. Reject traversal, absolute/drive paths,
links, duplicate/ambiguous members and missing roots before invoking CAD. Preserve portable
reference edges and report missing references with the referring document. Temporary worker
paths must never become engineering identity.

## Local preprocessing versus HTTP analysis

The client preprocessor is installed separately from the server worker. Register its full
executable directory with the Local CAD Agent using the same administrator-assigned worker ID.
The agent invokes it with source path and output directory arguments. It emits a one-line JSON
dependency report and creates the isolated native package in that workspace. Resolve/rebind
only package copies, reopen them without satisfying links from original paths, and declare
`isolated_reopen_validated` only after this succeeds. Optional local `part-metadata.json` supports
duplicate checking. No worker key is needed by this executable.

HTTP `POST /analyze` is a separate optional capability. Advertise `dependencyAnalysis: true` only
when it is implemented. Modelryx forwards an authenticated ZIP plus UTF-8 root path encoded in
`X-Modelryx-Root-Path-B64`; the response provides files, reference edges and missing references.
This enables assembly replacement review/reconciliation, not automatic Item creation.

## Native snapshot and normalized metadata

Define SDK-free models for native facts before producing PartMetadata. Retain available:

- document type, runtime/version, native units, configuration/model state and document properties;
- part number, revision, description, material, parameters and feature evidence;
- mass/density/volume/area, center and bounds with source units;
- drawing sheets, order, dimensions, title blocks, annotations and native view evidence;
- occurrence and definition IDs, ordered parent hierarchy, package-relative definition paths,
  display/reference names, per-parent quantities and documented transforms;
- suppression/visibility/exclusion/reference/phantom/substitute semantics and family/configuration
  identity, preserving uncertainty rather than guessing.

Nullable vendor collections must not cause extraction crashes. Unknown fields stay absent/null;
record warnings at the narrowest affected field or collection. Normalization is deterministic,
converts dimensions to millimetres and physical values to schema units, and distinguishes
complete/partial/unavailable/not-applicable states honestly. Validate against the server's
[PartMetadata v1](../architecture/partmetadata-v1.md) schema, not a private drifting schema copy.

Repeated placements share definition identity but retain separate occurrence identity. Do not
use temporary paths, list positions or display names as sole durable identifiers. A managed
placement can use `native.transform_convention: parent-row-major-mm` and matching occurrence IDs
with finite parent-relative homogeneous 4x4 transforms; without verified placement, the server
records it as unavailable rather than inventing coordinates.

## Outputs and publication

Generic jobs require JSON roles `native-snapshot`, `part-metadata`, `model-preview`, and prefer
GLB when advertised. A preview contains `schemaVersion: 1`, a root occurrence ID and uniquely
identified items/parents. GLB is derived browser geometry; PDF applies to drawings where supported.
STEP/component bundles may serve geometry translation inside the worker. Keep their source and
generator provenance and preserve hierarchy, transforms, units and geometry identity.

Return only requested, produced roles with media type, positive size, SHA-256, job-scoped download
URL and source/generator provenance. A partial result still contains every required role.
Modelryx validates identities, URLs, hashes, sizes and schemas before publishing. Download links
must stay on the configured worker origin and must not require Modelryx object-storage access.

Native occurrences are evidence for managed BOM review. Generic publication warns about unmatched
components and does not automatically create generated Items or nested component revisions.
Operators resolve direct occurrences to existing engineering identities; nested subassemblies
retain their own review boundary.

Current generic server validation accepts native snapshot, PartMetadata, model preview, GLB and
drawing PDF. Preferred-output selection can request component/related-drawing bundles, but that
allowlist currently rejects them when returned. Advertising arbitrary roles does not add server
publication support. Qualify the exact consumer profile before promising end-to-end exports.

## HTTP lifecycle and reliability

Authenticate with `X-Modelryx-Worker-Key`, matching `MODELRYX_INTERNAL_WORKER_KEY`; public health
and readiness probes are exceptions. There is no worker payload-signing/timestamp HMAC protocol.
Use TLS/private networks across hosts. [Worker security](../security/worker-security.md) explains
shared-secret limits and the separation from user JWT authorization.

`POST /api/v1/jobs` returns 202 with a job-scoped Location. Poll the status endpoint; download
finalized outputs from a publishable result. States include `Queued`, `Reconstructing`,
`Extracting`, `Validating`, `ReadyToPublish`, `Partial`, `Failed`, `Cancelled`, `Expired`.
Exact replays return retained jobs; conflicting job-ID reuse is rejected. Cancellation is
idempotent, including terminal jobs. Separate attempt identity, execution retries and retention;
do not confuse the STEP service's optional Idempotency-Key with the native job envelope.

Persist state atomically, recover interrupted work according to policy, enforce expiry and
capacity, and return structured errors with stage/retryability. A health response does not prove
native licensing or semantic correctness. Do not log shared credentials or expose local paths
through client-visible diagnostics.

## Verification and implementation order

1. Specify supported sources/kinds, root packaging rules, identity conventions and honest limits.
2. Implement recorded native snapshots, normalization and metadata/schema validation without CAD.
3. Implement native extraction/export and fixture-based checks on a qualified runtime.
4. Add bounded durable HTTP jobs, replay/conflict checks, cancellation and manifests.
5. Add the separately installed local preprocessor and optional HTTP analyzer only when supported.
6. Register against Modelryx and verify actual immutable publication and managed BOM review.

Use the [SDK conformance probe](../../worker-sdk/README.md#sdk-development), then qualify real
parts, repeated/rotated/scaled occurrences, nested assemblies, missing references, drawings,
negative coordinates, configuration changes, native crashes/timeouts and cancellation.
Check source immutability, package reopening, output hashes, geometry binding and restart recovery.
An engineering-search evaluation corpus is not a worker HTTP conformance harness.

See [testing](../development/testing.md), [processing architecture](../architecture/cad-processing-framework.md),
[FreeCAD](../../workers/freecad/README.md) and [STEP](step-worker.md).
