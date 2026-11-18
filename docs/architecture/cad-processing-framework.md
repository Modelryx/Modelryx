# Modelryx CAD processing framework

This document describes the current cross-component CAD processing architecture. Component startup,
installation, and troubleshooting belong in each worker's integration guide.

## Architecture decision

Modelryx uses independent HTTP services for CAD processing. STEP ships in the main Compose stack;
FreeCAD and other CAD workers are deployed separately. The browser's Local CAD Agent invokes
separately installed native preprocessors only for dependency resolution and source packaging.
Controlled CAD editing sessions are disabled in the first release.

The governing boundary is:

> CAD services extract native evidence and generate representations. Modelryx validates and
> publishes engineering records.

## Current topology

```text
Browser, upload client, or CAD plugin
                  |
                  v
       +------------------------+
       | Modelryx Server        |
       |                        |
       | authorization          |
       | Items and Revisions    |
       | source snapshots       |
       | processing jobs        |
       | validation/publication |
       +-----------+------------+
                   |
        +----------+--------------------------+
        |                     |               |
        v                     v               v
  FreeCAD server        STEP service    Custom CAD workers
  (external HTTP)       (durable jobs)     (HTTP services)
                                        (FreeCAD, etc.)
```

FreeCAD is not included in the platform runtime. Native
execution remains isolated so crashes, dependencies, timeouts, memory use, and concurrency can be
controlled.

## Modelryx responsibilities

Modelryx is authoritative for:

- authentication, authorization, engineering-record access and worker credentials;
- Items, Revisions, source and content snapshots, checkout, lifecycle, and approvals;
- processing-job orchestration and user-visible status;
- Artifact ownership, lineage, retention, and immutable publication;
- validation of PartMetadata, previews, geometry, drawings, and component results;
- generated component identity, BOM publication, comparison, where-used, and search;
- idempotency, audit events, and transactional publication.

Modelryx does not persist vendor SDK objects. A worker cannot allocate Item numbers, choose a
Revision, approve engineering content, or write Modelryx tables and storage records directly.

## Worker responsibilities

A worker or conversion service owns:

- application discovery, version compatibility, and native licensing checks;
- reconstruction of a validated dependency package in a bounded workspace;
- native process or SDK lifecycle, including cancellation, timeout, and cleanup;
- extraction of native properties, dependencies, occurrences, drawing evidence, and diagnostics;
- generation of supported neutral geometry, previews, drawings, and metadata;
- a manifest that identifies output roles, sizes, hashes, versions, warnings, and failures.

The worker returns evidence and representations. Modelryx verifies them before durable publication.

## Dispatch rules

Source format and available workers determine the processing route:

| Input | Primary route |
| --- | --- |
| `.fcstd` | Enabled matching external registration; otherwise a configured external FreeCAD URL; unavailable without either |
| `.step`, `.stp` | STEP service |
| Other native formats | Enabled external worker matched by CAD system and extension; legacy dedicated routes remain where supported |

Starting an external HTTP service does not register it. Re-register after capability changes.
Multiple matches are errors; external failures are surfaced without local native fallback.
The Local CAD Agent retains the selected
CAD-system filter.

Worker selection is a server decision. Clients identify source content and configuration context;
they do not choose arbitrary worker endpoints.

## Processing lifecycle

1. Modelryx commits or selects an immutable source snapshot and creates a processing job.
2. The server selects a compatible route from configuration, source type, and worker capability.
3. It sends either a single source document or a dependency package with a logical root path.
4. The worker validates the input and executes in a unique bounded workspace.
5. The worker exposes or returns a terminal result with typed output roles and diagnostics.
6. Modelryx downloads or reads the outputs and verifies their declared sizes, hashes, media types,
   schemas, and package safety.
7. Modelryx publishes Artifacts, metadata, component relationships, and BOM evidence in an
   idempotent transaction.
8. Temporary workspaces and expired worker jobs are reclaimed according to component policy.

Modelryx job state and worker-local job state are correlated but distinct. A worker completion is
not successful publication until Modelryx validation and commit finish.

## Worker contracts

Standalone services use versioned HTTP job contracts under `/api/v1`. These contracts are defined
in the [worker SDK](../../worker-sdk/README.md) and share validation,
security, and ownership invariants.

When custom CAD workers are deployed, Modelryx dispatches submissions based on file type and
worker capability. It validates the job identity, converter version, output paths, declared sizes,
and hashes before using the existing publication flow.

### External FreeCAD server

The standalone worker owns FreeCAD and converter dependencies, native extraction and generated
representations. It requires neither platform database credentials nor object storage access.
Register it in External CAD Workers, or configure the external FreeCAD URL for the PFG client.
An empty URL does not select local processing. The local preprocessor contains only the executable
host, packaging entry point, dependency packager and its shared helper; it extracts no metadata.

Dependency analysis is available through `POST /api/v1/cad/analyze` with a configured external
service. The logical root path is UTF-8 Base64 encoded in `X-Modelryx-Root-Path-B64`.

### STEP service

The STEP geometry worker is an authenticated conversion service with durable job state rather than a
`CADWorkerAdapter`. It accepts STEP input and returns geometry, preview, metadata, and diagnostic
outputs for Modelryx to validate and publish.

### Shared contract invariants

Every worker integration must:

- advertise a stable identity, implementation version, contract version, operations, and formats;
- treat source content as immutable and operate only on bounded temporary copies;
- verify declared input and output sizes and SHA-256 hashes;
- enforce time, workspace, package-expansion, and concurrency limits;
- return bounded diagnostics without secrets or unrestricted local paths;
- make cancellation, retryability, and terminal failure explicit;
- preserve enough native evidence for Modelryx to explain warnings and validate normalization.

Vendor commands and SDK objects remain behind the worker boundary. Vendor extensions may appear in
a bounded, versioned native-options or raw-evidence object, but they are not Modelryx's persistent
product model.

## Packages and root documents

Assemblies and drawings may require multiple source files. A package preserves safe logical paths
and identifies exactly one root document. Analysis runs near the native CAD engine when accurate
dependency discovery requires vendor behavior.

All package consumers reject:

- absolute, UNC, or drive-qualified names;
- parent traversal and entries escaping the workspace;
- duplicate or ambiguous normalized paths;
- excessive entry counts, expanded bytes, or compression ratios;
- missing roots and roots whose type is unsupported by the selected worker.

Package validation ensures safe dependency handling and prevents path traversal attacks.

## Result and publication model

Outputs are identified by semantic role, not by guessed filenames. Depending on the worker and
operation, results can include:

- normalized PartMetadata and native raw evidence;
- GLB or Modelryx Graphics packages and preview descriptors;
- STEP, STL, PDF, or raster drawing representations;
- component packages and occurrence/BOM evidence;
- bounded diagnostics and warnings.

Not every CAD system can produce every representation. Capability reporting establishes what is
supported; absence of an optional output is different from a corrupt or invalid required output.

Modelryx validates normalized data against its own schemas. Raw vendor evidence remains available
for diagnostics and future normalization but does not become an authoritative product model merely
because a worker returned it.

## Reliability and idempotency

- Inputs and outputs are content-addressed with SHA-256 and verified before use.
- Submission and publication use stable idempotency identities.
- Retries cannot create duplicate durable Artifacts, component Items, or BOM records.
- Cancellation is best effort during native calls and must converge on an explicit terminal state.
- Timeouts terminate or quarantine native work before the workspace is reclaimed.
- Diagnostics distinguish configuration, licensing, invalid input, timeout, cancellation, transient
  infrastructure, and deterministic processing failure.
- A worker becoming unavailable does not invalidate already-published engineering data.

## Security boundary

- Worker endpoints are internal services and are not exposed directly to browser users.
- CAD plugins authenticate to Modelryx, not to worker hosts.
- Worker credentials are scoped, rotatable, and separate from user credentials.
- Native applications operate on temporary copies and never save into the source snapshot.
- Packages and outputs are bounded and validated before extraction or publication.
- Secrets, unrestricted local paths, and unbounded native logs are excluded from results.
- Administrative status probes report `NOT_CONFIGURED`, `OFFLINE`, incompatible, limited, or online
  states without treating application startup alone as proof of processing readiness.

## Extending the framework

A new licensed desktop integration should normally follow the standalone service contract. A
shippable open-source engine uses an isolated external service. A cloud CAD system should use an
API connector behind the same Modelryx ownership and publication boundary.

New integrations must not add vendor SDK dependencies to Modelryx or introduce a second durable
product model. Refer to the worker SDK documentation for adding custom worker implementations.

## Related documents

- [PartMetadata v1](partmetadata-v1.md)

## Generic external publication limits

Native extraction evidence is separate from managed BOM identity. The generic external path warns
about unmatched components and supports operator-resolved relationships; it does not automatically
create generated component Items/revisions. Dedicated external URL paths can have different publication
behavior.

The current generic result allowlist accepts native snapshot, PartMetadata, model preview, GLB and
drawing PDF. Preferred-output selection can also request component geometry/related-drawing bundles,
which generic validation currently rejects if returned. Advertised worker support alone is not
end-to-end publication support. See the SDK and qualify the intended output profile.
