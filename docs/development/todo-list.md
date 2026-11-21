# Project todo list

## Project review and priorities

Reviewed on 2026-11-20 through targeted source and documentation inspection of
the API, persistence, processing, storage, authentication, AI, frontend, and CI.
This is a prioritized development backlog, not a full security audit or a claim
that runtime qualification has passed. Existing capabilities are identified below
so follow-up work extends them rather than implementing parallel systems.

P1 means a reliability, data-protection, or release-qualification priority before
wider production use. P2 means the next capability or scalability increment.
Items are proposed and have no committed delivery dates. Acceptance criteria are
targets, not measured results. Existing AI learning and CAD annotation proposals
are retained after the project-wide additions.

| ID | Priority | Enhancement | Main reason |
| --- | --- | --- | --- |
| FE-01 | P1 | Data-preserving schema upgrades | Initializer deliberately rejects incompatible schemas |
| FE-02 | P1 | Coordinated backup and verified recovery | Database and object files must be restored consistently |
| FE-03 | P1 | AI history visibility, retention, and embedding compatibility | Stored answers and vectors need source/access/version safeguards |
| FE-04 | P1 | Shared admission limits and capacity protection | Existing per-request limits do not allocate capacity across users |
| FE-05 | P1 | Reproducible release qualification | Some search release gates remain external; browser workflows need qualification |
| FE-06 | P2 | Per-worker identity and credential rotation | Shared worker credentials couple revocation and rotation |
| FE-07 | P2 | Complete object-storage backend integration | S3 utilities exist, but application workflows remain local |
| FE-08 | P2 | Scalable observability and operational alerts | Existing health/metrics need durable latency, queue, and AI cost signals |
| FE-09 | P2 | Assistant tool coverage and snapshot-aware conversations | Initial tools cover search/revision lookup, with request-local model context |
| FE-10 | P2 | Drawing visual revision comparison | Structured/text comparison exists; visual page diff is explicitly unavailable |

## FE-01: Data-preserving schema upgrades

**Status: proposed; fresh initialization and matching-schema validation exist.**

The [initializer documentation](../operations/database-initialization.md) explicitly
rejects historical or incompatible schemas and provides no incremental upgrade
workflow. This protects existing data, but deployed installations need a supported
way to adopt future model and index changes.

- Establish a versioned migration baseline and supported upgrade paths from
  released schema fingerprints. Keep fresh initialization and migration ownership
  consistent; do not bypass fingerprint rejection or recreate production volumes.
- Separate deployment-time schema privileges from normal API/worker privileges.
- Preflight extension/runtime compatibility, required disk space, incompatible
  application versions, and long-running jobs. Use an explicit maintenance window
  for initial support unless online compatibility is demonstrated.
- Provide resumable data backfills and validation of snapshot membership, hashes,
  foreign keys, numbering counters, and search projections.
- Define rollback or restore requirements for irreversible transformations and
  document the supported application/schema version combinations.

Acceptance: upgrade a populated fixture from each supported predecessor without
losing artifacts, history, permissions, or numbering state; test interrupted and
concurrent upgrades and verify the resulting fingerprint. Requires FE-02 recovery
qualification before production upgrade support is declared.

## FE-02: Coordinated backup and verified recovery

**Status: proposed; built-in backup and restore are outside the initial release.**

[Deployment](../operations/deployment.md) uses separate PostgreSQL and object-data
volumes. Restoring either in isolation can leave verified database records pointing
to absent content or omit recently committed records.

- Define a consistency protocol using a quiesced publication window or a proven
  point-in-time strategy for database records and immutable object content.
- Produce a backup manifest containing application/schema versions, object hashes
  and sizes, backup boundaries, and completion status. Protect credentials and
  encryption keys through a separate documented recovery procedure.
- Add retention, encryption, off-host copies, failure reporting, and a documented
  recovery-point objective and recovery-time objective for each deployment class.
- Restore into an isolated target, validate schema compatibility, file integrity,
  snapshot references, authorization, and representative downloads/previews.
  Rebuild disposable indexes without modifying historical engineering records.
- Reconcile restored in-flight jobs before dispatch so a recovery does not replay
  completed external operations or publish unfinished outputs.

Acceptance: a dated restore drill from a populated backup proves the chosen
recovery objectives, detects missing/corrupt objects, and recovers working
downloads, history, and previews. A successful backup command alone is insufficient.

## FE-03: AI history privacy and embedding compatibility

**Status: proposed hardening of existing assistant and retrieval services.**

Current [conversation reads](../../server/src/server/modelryx/api/assistant.py)
check ownership and return stored content/response snapshots. The tool loop checks
live sources, but historical answer replay needs a separate source-visibility
policy. The [legacy vector retrieval path](../../server/src/server/modelryx/services/ai.py)
stores embedding model names but selects candidates by non-null vectors without
requiring the configured model identity.

- Reauthorize referenced sources when reading historical messages. Redact or
  withhold stored answer text that depends on inaccessible or deleted sources;
  conversation ownership must not preserve revoked engineering access.
- Add retention, deletion, and bounded pagination for conversations/messages and
  tool traces. Keep raw query/content retention explicit and restricted.
- Version embedding spaces by provider/model, dimensions, preprocessing, and
  chunking. Restrict candidate vectors to the active compatible space; equal
  dimensions do not make two embedding models interchangeable.
- Validate embedding cardinality, ordering, finite numeric values, and dimensions;
  normalize malformed-provider failures and retain lexical operation on failure.
- Build a new generation before switching spaces, expose coverage during rebuild,
  and retain a tested rollback path. Coordinate with the knowledge-refresh work
  below rather than creating another indexing pipeline.

Acceptance: revoked/deleted sources cannot leak through history or subsequent
provider payloads; incompatible vectors are never compared; switching models and
partial/failed backfills preserve correct lexical results and explicit warnings.

## FE-04: Shared admission limits and capacity protection

**Status: proposed extension of existing request limits and authentication controls.**

The server already has upload/extraction limits, login lockout, bounded tool calls,
and durable processing jobs. See [authentication](../../server/src/server/modelryx/api/auth.py),
[settings](../../server/src/server/modelryx/core/config.py), and [tools](../ai/tools.md).
Add shared controls for concurrent and sustained workload across API replicas.

- Define configurable request, upload-byte, active-job, and expensive-query limits
  by authenticated actor or deployment scope, plus appropriate unauthenticated
  login/network limits. Preserve existing account lockout and non-disclosing errors.
- Reserve capacity before accepting expensive processing, bundle generation, or
  provider calls. Expose retryable responses and fair scheduling rather than
  allowing one user or oversized assembly to monopolize workers.
- Add disk-watermark admission checks for staging and processing workspaces;
  retain recovery capacity and avoid deleting authoritative content to free space.
- Enforce AI token/spend budgets, provider concurrency, and cancellation through
  the HTTP request, model loop, and supported downstream work.
- Make shared counters/leases safe under multiple API processes and worker restarts.

Acceptance: concurrent load across two API instances respects configured limits,
returns actionable retry information, releases abandoned reservations, and does
not exceed storage/provider budgets or starve unrelated users.

## FE-05: Reproducible release and browser-workflow qualification

**Status: proposed extension of substantial existing automated CI.**

[Platform CI](../../.github/workflows/ci.yml) already checks database-backed backend
tests, frontend type/build/lint/contracts, worker contracts/native STEP conversion,
and security scanning. The [search release evidence](../ai/engineering-search.md)
still lists production-like latency, user studies, sign-off, and defect review as
external gates. The inspected [frontend scripts](../../front-end/package.json)
do not define a browser end-to-end runner.

- Add isolated browser tests for sign-in, upload/staging, duplicate resolution,
  processing recovery, checkout/check-in, revision history, approval, download,
  and assistant references/navigation. Cover supported roles and negative cases.
- Exercise keyboard navigation, focus restoration, modal behavior, small screens,
  failed requests, and stale workspace responses; extend existing accessibility
  affordances with automated checks and manual assistive-technology review.
- Add deterministic model fixtures for every adapter's tool protocol and a
  separately enabled synthetic-data provider connectivity smoke test. External
  provider outages must not make the ordinary offline suite nondeterministic.
- Publish benchmark profiles with dataset, hardware, application commit, worker
  versions, and corpus identity. Complete existing external gates without changing
  their recorded status until dated evidence is attached.
- Record dependency/known-advisory disposition, reproducible build inputs, and
  native runtime qualification for each release. Recheck the dated assessment in
  [tooling security](tooling-security.md); do not infer a current clean bill of
  health from an older review or a runtime-only dependency scan.

Acceptance: critical role/workflow scenarios pass against an isolated initialized
database and workers; release evidence identifies the exact build and includes
the required performance, usability, and security decisions.

## FE-06: Per-worker identity and credential rotation

**Status: proposed; shared-key worker authentication and transport guidance exist.**

The [worker security contract](../security/worker-security.md) explicitly uses one
shared header key and has no per-worker credential rotation API.

- Introduce registration-bound worker credentials, scoped to the permitted worker,
  callback/job operations, and deployment. Independent workers still receive no
  database credentials or unrestricted object-store access.
- Support individual revocation and controlled overlapping rotation without
  stopping every worker at once. Keep secrets out of discovery responses and logs.
- Qualify private-network/TLS deployment, and evaluate mTLS or signed requests with
  bounded timestamp/nonce replay checks where deployment requirements justify it.
- Version the SDK authentication contract and provide a limited transition path
  from shared keys without silently weakening authorization.

Acceptance: one compromised/revoked worker cannot impersonate another; forged or
out-of-scope callbacks fail; rotation completes while other workers continue jobs.

## FE-07: Complete object-storage backend integration

**Status: proposed; local storage is integrated and S3 utilities already exist.**

[S3ObjectStorage](../../server/src/server/modelryx/services/s3_storage.py) explicitly
states that application workflows still use local storage. Several workflows rely
on the path-returning [local storage abstraction](../../server/src/server/modelryx/services/storage.py).

- Refactor upload, artifact publication, attachments, bundles, download/range reads,
  retention, and purge behind backend-neutral operations rather than local paths.
- Keep temporary worker/staging files local where needed, but distinguish those
  workspaces from authoritative object content and publish only verified objects.
- Integrate S3-compatible private buckets with multipart retry/abort behavior,
  checksum verification, least-privilege credentials, and bounded authorized URLs
  only if that delivery mode is explicitly supported.
- Provide a local-to-object-store migration manifest, integrity verification,
  reconciliation, and a defined rollback strategy. Coordinate backup and deletion
  semantics with FE-02 rather than relying on bucket versioning alone.

Acceptance: the same artifact lifecycle contract tests pass for local and an
isolated S3-compatible service, including interrupted uploads, missing objects,
range requests, purge, and unauthorized access. No backend switch is advertised
until the full application workflow works without shared authoritative local files.

## FE-08: Scalable observability and operational alerts

**Status: proposed extension; health, worker status, request IDs, and metrics exist.**

[Operations](../../server/src/server/modelryx/api/operations.py) already exposes
Prometheus-format metrics, queue/worker information, and approval/notification
health. Some search metrics are reconstructed from retained session histories;
they do not provide durable lifetime counters or a bounded-cost scrape strategy.

- Add bounded-cardinality counters/histograms for API latency, job queue age,
  processing stages, retries, worker timeouts, publication failures, and AI/tool
  latency, failures, token usage, and estimated cost where usage is available.
- Avoid scanning all retained histories on each metrics scrape. Separate lifetime
  operational telemetry from records that expire under product retention policy.
- Propagate correlation across API requests, durable jobs, external workers,
  indexing generations, and provider requests without logging raw engineering
  content, credentials, or unbounded identifiers as metric labels.
- Supply deployable dashboards, alerts, and runbooks for stalled jobs, missing
  heartbeats, low disk, index lag, backup failure, and provider budget exhaustion.
  Add authorized diagnostics that distinguish configuration from connectivity
  and qualified native capability; an ONLINE card alone is not proof of conversion.

Acceptance: injected failures generate attributable signals and actionable alerts;
metrics remain responsive at the agreed retained-history size, and telemetry
does not reveal sensitive content or reset silently when product history expires.

## FE-09: Assistant tool coverage and snapshot-aware conversations

**Status: proposed extension of the implemented adapter/registry/loop.**

The [tool catalog](../ai/tools.md) currently exposes search and explicit revision
lookup. Specialized deterministic handlers remain in use; model context is limited
to the new question and fresh workspace identifiers. Historical-content assistant
questions remain restricted by the current revision-based context contract.

- Add read-only BOM, where-used, deterministic comparison, document-evidence, and
  validation tools by adapting existing authorized domain services.
- Version context to include immutable content snapshots and verify selected
  occurrences belong to the specified assembly snapshot. Preserve current-workspace
  precedence and explicit ambiguity handling.
- Add owner-scoped paginated conversation listing, reopen/delete controls, and
  bounded cross-message context only after FE-03 history reauthorization is in place.
- Make citations navigable to an exact evidence/source/version and disclose
  applied filters, partial coverage, and tool failures. Evaluate claim support
  beyond merely checking that a citation ID exists.
- Add cancellation, provider usage accounting, prompt versions, and provider-policy
  controls. Move long operations to cancellable jobs if hard deadlines are required;
  the current synchronous tool deadline is cooperative.
- Integrate confirmed proposals through the existing action engine only as a
  separately gated increment with fresh authorization, state preconditions,
  idempotency, and audit. Controlled CAD editing remains disabled until its external
  worker contract is independently qualified.

Acceptance: equivalent domain and tool requests return the same authorized
evidence, historical questions cite the exact snapshot, follow-ups do not inherit
stale/unauthorized context, and provider failures retain useful deterministic paths.

## FE-10: Drawing visual revision comparison

**Status: proposed; file, text, and normalized-entity comparisons already exist.**

The [drawing comparison implementation](../../server/src/server/modelryx/comparison/service.py)
explicitly reports that visual page diff is unavailable, while exposing its
existing comparison level and structured evidence.

- Add bounded, isolated rendering of verified drawing/PDF versions and stable
  page/view matching before computing visual overlays and change regions.
- Handle page additions/removals, rotation, scale, rendering differences, fonts,
  and scans. Label OCR-derived evidence and unsupported content explicitly.
- Link every visual change to artifact hash, revision/content identity, page,
  and region. Present visual changes separately from authoritative normalized
  dimension, tolerance, GD&T, and annotation changes.
- Cache by source hashes plus renderer/algorithm versions, support cancellation,
  and expose comparison coverage and uncertainty in the existing comparison UI.

Acceptance: qualified drawing fixtures detect intended changes and tolerate
documented rendering-only differences, while text/structured comparison continues
working when visual comparison is unavailable. This is separate from displaying
native CAD annotations in the 3D preview below.

## AI knowledge refresh and learning from feedback

**Status: planned; automatic learning is not implemented.**

Modelryx already provides configurable OpenAI, Claude, and Gemini chat adapters
and an optional read-only tool loop. These integrations do not train a model or
automatically turn conversations into reusable knowledge. Existing indexing and
search feedback provide foundations for the enhancements below.

The goal is to keep engineering knowledge current and improve interpretation and
tool selection through verified feedback, while preserving authoritative records,
authorization, and deterministic behavior.

### Knowledge refresh

- Extend existing indexing with durable work triggered by committed revision,
  metadata, and document changes. Use idempotent jobs, retries, reconciliation,
  and versioned projection activation so failed or out-of-order jobs cannot
  replace newer evidence or invalidate successful CAD publication.
- Bind indexed content to source versions or hashes and extraction, chunking,
  and embedding versions. Expose freshness, failed indexing, and partial coverage.
- Recheck current access before retrieval and provider calls. Invalidate or purge
  deleted sources and ensure permission changes take effect even before reindexing.
- Treat refreshed indexes as derived knowledge, not changes to model weights.

### Feedback and scoped memory

- Capture explicit feedback for incorrect answers, misunderstood filters, wrong
  tool choices, missing evidence, and user-corrected interpretations. Extend the
  existing search-feedback mechanism rather than adding a competing system.
- Store feedback as candidate learning examples with provenance, relevant source
  versions, the proposed correction, and review status. A positive rating or
  result click alone must not establish correctness.
- Support reviewed terminology, preferences, and successful question-to-tool
  examples scoped to the appropriate user, project, or organization. For example,
  a project may define “motor cover” as its housing classification.
- Retrieve only authorized, relevant approved memories during interpretation.
  Keep them separate from system policy and engineering evidence; memory cannot
  grant access, introduce executable tools, or override domain validation.
- Provide review, correction, expiry, deletion, and retention controls. Retaining
  raw questions or source excerpts requires an explicit data-retention policy.
- Keep factual corrections separate from terminology and preferences. A chat
  statement that a part weighs 3 kg must not overwrite its recorded mass; changes
  to engineering records require the normal authorized domain workflow.

### Evaluated improvement pipeline

```text
Question -> Tools -> Answer -> Explicit feedback
                                  |
                                  v
                       Candidate learning example
                                  |
                                  v
                    Evidence validation and review
                                  |
                                  v
                    Offline regression evaluation
                                  |
                                  v
                 Versioned activation or rejection
```

- Convert confirmed defects into reviewed regression cases with independently
  specified expected filters, tool calls, results, warnings, and evidence.
- Evaluate proposed changes to prompts, terminology mappings, examples, retrieval,
  or ranking against development data and a separate locked release corpus.
- Measure critical-filter correctness, tool selection, authorized result parity,
  citation validity, grounded claims, latency, and cost. Include ambiguous names,
  negation, unknown feature coverage, prompt injection, and revoked access.
- Automate collection, indexing, candidate evaluation, and reporting. Activate
  improvements only after defined quality and security gates and the applicable
  review policy pass; never promote raw model-generated lessons directly.
- Version active learning artifacts, record evaluation evidence, and support
  rollback. Maintain deterministic fallbacks and test all configured provider
  protocols without assuming that a prompt change transfers equally well.

### Optional model training

Consider model training only after evaluations demonstrate a persistent behavior
gap that retrieval, tool design, and reviewed examples cannot address. Any training
workflow must use curated, authorized datasets and a provider or local model that
supports it. Training is a separate, explicitly enabled process with data-use,
cost, evaluation, deployment, and rollback controls; it must not run automatically
on every conversation. Changing engineering facts should continue to be handled
through authoritative records and knowledge refresh.

### Suggested delivery order and acceptance

1. Reliable knowledge refresh and visible indexing freshness.
2. Feedback capture and a review queue for candidate examples.
3. Scoped terminology and example memory with access and retention controls.
4. Evaluated, versioned improvement activation with rollback.
5. Optional model-training experiments if measured gaps justify them.

Each increment must demonstrate source traceability, isolation of private memory,
correct handling of deleted or revoked sources, and no mutation of engineering
facts through feedback. Behavioral changes must pass the existing search release
gates and applicable tool tests before activation. Knowledge refresh must recover
from duplicate jobs, crashes, and stale events without rolling back CAD results.

See [AI design specification](../ai/design-spec.md),
[provider configuration](../ai/providers.md), [read-only tools](../ai/tools.md),
and [search reliability](../ai/engineering-search.md). This enhancement has no
committed release date.

## CAD text and annotations in 3D previews

**Status: planned; not supported by the current FreeCAD 3D preview.**

Add CAD-neutral support for displaying text and annotations in the 3D viewer, and
implement extraction for the external FreeCAD worker. Some FreeCAD annotations do
not contain a CAD shape, so the current geometry exporter omits them from the 3D
preview. The native file remains available for viewing in FreeCAD. TechDraw PDF
export is a separate feature and does not add annotations to the 3D preview.

The enhancement should cover:

- FreeCAD annotation labels (`App::Annotation`), Draft text and labels, and Draft
  linear and angular dimensions, including their lines, arrows and displayed values.
- Text content and formatting, placement and orientation, visibility, scale,
  color, alignment, and parent and child relationships, using native information
  where available. Do not invent missing values or silently display unsupported
  annotations as accurate.
- A CAD-neutral worker output and viewer representation that supports selection,
  visibility controls and fit-to-view without tying the platform to FreeCAD.
- Diagnostics for annotations that cannot be represented faithfully, while
  retaining the rest of the preview.
- Tests for text, multiline labels, dimensions, placements in different planes,
  hidden items, nested groups, and viewer selection and fit. Include a FreeCAD
  fixture containing Draft objects and annotations.

Rendering fidelity and supported fonts should be documented as part of the
implementation. This enhancement has no committed release date.
