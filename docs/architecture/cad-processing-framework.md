# Modelryx CAD processing framework

This document describes the current cross-component CAD processing architecture. Component startup,
installation, and troubleshooting belong in each worker's integration guide.

## Architecture decision

Modelryx uses two deployment models:

1. **Embedded processing** for technology shipped and operated with Modelryx, including the
   FreeCAD adapter and STEP geometry service.
2. **Custom CAD workers** can be deployed as independently scalable HTTP services following the
   [worker integration guide](../workers/new-cad-worker-guide.md).

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
  FreeCAD adapter       STEP service    Custom CAD workers
  and analyzer          (stateless)     (HTTP services)
                                        (FreeCAD, etc.)
```

Embedded means shipped and deployed with Modelryx, not loaded into the web API process. Native
execution remains isolated so crashes, dependencies, timeouts, memory use, and concurrency can be
controlled.

## Modelryx responsibilities

Modelryx is authoritative for:

- authentication, authorization, tenant boundaries, and worker credentials;
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
| `.fcstd` | FreeCAD adapter |
| `.step`, `.stp` | STEP service |
| Other formats | Custom workers (deploy following integration guide) |

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
in the [CAD worker integration guide](../workers/new-cad-worker-guide.md) and share validation,
security, and ownership invariants.

When custom CAD workers are deployed, Modelryx dispatches submissions based on file type and
worker capability. It validates the job identity, converter version, output paths, declared sizes,
and hashes before using the existing publication flow.

### Embedded FreeCAD adapter

The FreeCAD processing path uses the `CADWorkerAdapter` protocol selected by
`MODELRYX_CAD_WORKER_ADAPTER`. A compatible adapter owns native conversion and controlled-edit
execution and publishes its capability manifest through the Modelryx worker heartbeat API.

Its normalized conversion envelope contains:

- a Modelryx Graphics (`.pfg`) package with `manifest.json`, `model.glb`, and
  `model-preview.json`;
- `<package>.part-metadata.json` using the PartMetadata schema;
- `<package>.cad-raw-metadata.json` for opaque adapter-specific evidence.

Dependency analysis is submitted to `POST /api/v1/cad/analyze`. The logical root path is encoded as
UTF-8 Base64 in `X-Modelryx-Root-Path-B64` so it does not depend on HTTP header character encoding.

### STEP service

The STEP geometry worker is a stateless authenticated conversion service rather than a
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
shippable open-source engine may use an isolated embedded service. A cloud CAD system should use an
API connector behind the same Modelryx ownership and publication boundary.

New integrations must not add vendor SDK dependencies to Modelryx or introduce a second durable
product model. Refer to the worker SDK documentation for adding custom worker implementations.

## Related documents

- [PartMetadata v1](partmetadata-v1.md)
