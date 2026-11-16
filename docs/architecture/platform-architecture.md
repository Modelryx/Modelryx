# Modelryx platform architecture

This document is the top-level technical map of the current Modelryx platform. It explains the
major deployable components, their ownership boundaries, persistent data, and primary request flows.
Detailed contracts and component setup are linked rather than repeated here.

## Architectural shape

Modelryx is a browser application backed by a modular FastAPI server. PostgreSQL and object storage
hold authoritative product state. Background worker processes execute durable processing jobs.
FreeCAD and STEP processing are shipped with the platform; additional CAD workers can be deployed
as independently scalable services.

```text
                                      optional AI gateway
                                              ^
                                              |
+----------------+      HTTPS/JSON      +-----+----------------------+
| React frontend | <------------------> | Modelryx FastAPI server   |
| and 3D viewer  |                      |                            |
+----------------+                      | auth and authorization     |
                                        | Items and Revisions        |
CAD plug-in / local selector ---------->| staging and publication    |
                                        | search, BOM, approvals, AI |
                                        +------+-------------+-------+
                                               |             |
                                      SQL      |             | files
                                               v             v
                                        +-------------+  +--------------+
                                        | PostgreSQL  |  | Object store |
                                        | + pgvector  |  | abstraction  |
                                        +------+------+  +------+-------+
                                               ^                ^
                                               | claim/jobs     | inputs/outputs
                                        +------+----------------+------+
                                        | Modelryx background workers |
                                        | general and FreeCAD roles    |
                                        +------+----------------+------+
                                               |                |
                                   HTTP        |                | HTTP
                                               v                v
                                        +-------------+   +-------------------+
                                        | STEP worker |   | Custom CAD workers |
                                        | container   |   | (FreeCAD, etc.)    |
                                        +-------------+   +-------------------+
```

The server is a modular monolith: API modules share one application, domain model, database, and
transaction boundary. CAD execution is distributed because native engines have distinct runtime,
licensing, operating-system, and failure-isolation requirements.

## Repository and component map

| Directory | Runtime responsibility |
| --- | --- |
| `front-end/` | React/Vite browser UI, authenticated API client, PDF preview, and Three.js-based viewer |
| `server/` | FastAPI application, SQLAlchemy models, schema initialization, domain services, orchestration, and background processing |
| `workers/freecad/` | FreeCAD dependency analysis, native conversion, and controlled CAD operations |
| `workers/step/` | Standalone authenticated STEP geometry and metadata service |
| `worker-sdk/` | Native worker HTTP contract, manifest validation and conformance probe |
| `local_cad_agent/` | Native file dialog, CAD-system selection and installed client preprocessor dispatch |
| `scripts/` | Project configuration, verification, public export and database backup/restore utilities |
| `docs/` | Cross-component current architecture, integrations, product specifications |

Component-specific build and installation instructions belong in each component's `README.md` or
integration guide. Cross-component decisions belong under `docs/architecture`.

## Frontend

The frontend is a TypeScript/React single-page application. It owns presentation state and user
interaction, including authentication, Item and Revision workflows, staging and uploads,
administration, search, PDF preview, and 3D viewing.

The frontend does not connect to PostgreSQL, object storage paths, or native CAD worker endpoints.
It submits through Modelryx and uses URLs issued by the API for bounded content operations.

## Modelryx server

The FastAPI application is the platform's authority and public application boundary. API modules
under `server/src/server/modelryx/api` expose versioned routes. Domain and orchestration logic lives
under `services`, `cad`, `metadata`, `duplicate_detection`, `engineering_search`, `knowledge`,
`comparison`, `bom_analysis`, `validation`, and `actions`.

The server owns:

- identity, authentication, roles, authorization, and record access;
- Item and Revision identity, numbering, lifecycle, checkout, and approval;
- Artifact lineage, content snapshots, attachments, downloads, and retention;
- upload staging, validation, duplicate decisions, and atomic commit;
- processing-job creation, worker dispatch, result validation, and publication;
- authoritative BOM and relationship records;
- metadata extraction history, comparison, search indexes, and audit events;
- optional AI retrieval, assistant orchestration, and controlled action confirmation.

API handlers coordinate request validation and authorization, then call domain services. Native CAD
SDK behavior and vendor object models do not belong in the server.

## Background execution

Processing jobs are durable database records rather than in-memory web requests. Worker processes
claim compatible jobs, update stages and heartbeats, read verified inputs, call the appropriate
converter or CAD service, validate outputs, and publish results.

The Compose deployment separates `general-worker` for general and STEP-oriented work from
`freecad-worker`, which runs in the FreeCAD-capable image. The database coordinates durable work and
recovery; there is no separate Redis or message-broker dependency in the current deployment. Job
claiming, idempotency, timeouts, and reconciliation must remain safe under multiple workers and
process restarts.

## CAD and geometry services

There are two primary integration shapes:

1. The FreeCAD adapter and analyzer are platform-shipped but runtime-isolated.
2. The STEP worker is an authenticated HTTP conversion service with durable job journals.

Additional CAD workers can be developed following the [worker integration guide](../workers/new-cad-worker-guide.md).

Modelryx selects a service, provides an immutable source or dependency package, and validates the
result. Workers extract native evidence and generate representations; only Modelryx publishes
Artifacts, metadata and managed BOM records. Generic external publication retains native
component evidence for operator review; it does not automatically create component Items.

## Persistence

### PostgreSQL

PostgreSQL is the system of record for users, configuration, Items, Revisions, Artifacts and their
relations, content snapshots, processing jobs, metadata records, BOM data, approvals, checkouts,
audit events, search documents, and AI/knowledge records. The initializer creates fresh schemas
and verifies their fingerprint. It refuses incompatible existing schemas; no Alembic upgrade
workflow is shipped. Data-preserving upgrades need a separately qualified process.

The development image includes pgvector for vector-backed engineering search when embeddings are
configured. Structured and lexical search continue to work without an external AI provider.

### Object storage abstraction

Large file content is stored outside relational tables through the server's object-storage
abstraction. The default Compose deployment uses a shared local volume. Database rows store content
identity, hashes, sizes, state, and storage keys; callers must not construct filesystem paths from
database values.

Content becomes readable only after verification. SHA-256, byte size, state, ownership, and
revision snapshot membership are validated independently of the physical storage backend.

### Worker-local storage

FreeCAD and STEP services may retain temporary job workspaces and diagnostics according
to bounded retention policies. Worker-local state is operational state, not authoritative Modelryx
product data.

## Core product model

```text
Item
  └─ Revision
       └─ Revision content snapshot
            └─ Revision–Artifact relations
                 └─ Artifact
                      └─ verified file content

Item or Revision
  └─ attachment / Additional File
       └─ immutable versions

Revision
  ├─ metadata extractions
  ├─ BOM views and relationships
  ├─ processing jobs
  ├─ approvals and comparison
  └─ CAD sessions and generated representations
```

An Item provides stable business identity. A Revision represents engineering configuration.
Content snapshots preserve immutable committed file membership. Artifacts represent typed,
lineage-aware engineering content; attachments represent ancillary Item- or Revision-scoped files.

For data model details, see the
[PartMetadata v1](partmetadata-v1.md).

## Primary flows

### Upload and commit

1. The frontend selects a root document and gathers required dependencies where applicable.
2. The API creates a staging session and bounded upload targets.
3. Uploaded bytes are verified by size and SHA-256.
4. The server classifies content, runs validation and duplicate review, and reserves identity only
   at the appropriate workflow stage.
5. Commit transactionally creates or updates the Item, Revision, content snapshot, and Artifact
   relations.
6. Compatible processing jobs are queued after the authoritative source commit.

### CAD processing and publication

1. A background worker claims a compatible processing job.
2. It materializes the committed source snapshot and chooses the configured CAD route.
3. Native or neutral processing produces typed outputs and diagnostics.
4. The worker verifies results; Modelryx validates schemas, hashes, and expected roles.
5. Publication records derived Artifacts, metadata, previews, component relationships, and BOM
   evidence idempotently.

### Change and approval

Checkout protects an editing scope. A commit creates a new immutable content state rather than
mutating an older snapshot. Approval requests, decisions, release policy, and audit events remain in
Modelryx regardless of which CAD system produced the files.

See the [worker integration guide](../workers/new-cad-worker-guide.md) for details on extending Modelryx with additional CAD workers.

### Search and AI

For AI architecture details, see [AI subsystem overview](../ai/overview.md).

## Security and trust boundaries

- Browsers and CAD plug-ins authenticate to Modelryx, never directly to worker hosts.
- Internal workers use a separate shared internal credential; user JWTs are not worker credentials.
- Every API operation performs authorization against managed Item and Revision visibility.
- File names, archives, CAD metadata, worker diagnostics, and AI output are untrusted input.
- Packages reject traversal, absolute paths, duplicate normalized entries, and configured limits.
- Native applications work on temporary copies and cannot publish directly to product storage.
- Secrets and unrestricted workstation paths are excluded from durable worker results.
- Audit events record security- and lifecycle-relevant operations.

## Deployment topology

The root `compose.yaml` defines PostgreSQL, database initialization, storage initialization, the FastAPI service,
general and FreeCAD workers, the FreeCAD preprocessor, and STEP worker. Custom CAD workers can be
deployed as independent services and are reached through configured URLs.

Operators can change TLS termination, secret delivery, process scheduling and network topology.
The current application uses a shared local object volume; replacing it with remote object storage
requires application integration, not only deployment configuration. They must preserve the ownership, authentication,
validation, immutability, and idempotency boundaries described here.

## Architectural invariants

1. Modelryx is the only authority that publishes product records.
2. Committed engineering content is immutable; change creates a new snapshot or Revision state.
3. Database metadata and file content are verified and correlated, never assumed consistent.
4. CAD-specific logic stays behind worker boundaries.
5. Background work is durable, restart-safe, observable, and idempotent.
6. Optional AI services can enrich or propose, but cannot bypass domain authorization and validation.
7. Current architecture documents describe implemented behavior; historical plans and proposals must be labeled separately.

## Related documents

- [Documentation index](../README.md)
- [CAD processing framework](cad-processing-framework.md)
- [PartMetadata v1 specification](partmetadata-v1.md)
- [Operations and recovery](../operations/deployment.md)
- [Troubleshooting and local startup](../../DEVELOPMENT.md)
