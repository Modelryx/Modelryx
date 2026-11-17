# CAD worker security

This describes the implemented worker boundary. Application JWT authentication is separate
from worker authentication. Worker requests do not use signed payload/timestamp HMAC headers.

## Credentials and transport

Protected CAD/geometry endpoints require the shared `MODELRYX_INTERNAL_WORKER_KEY` value in
`X-Modelryx-Worker-Key`. Windows APIs configure their expected secret using `Worker__ApiKey`.
The worker's geometry-client key must also match its converter's configuration.

```text
X-Modelryx-Worker-Key: <shared-secret>
```

This authenticates possession of a shared secret. It does not encrypt traffic or implement
request signing, timestamp checking, per-worker identities or cryptographic replay protection.
Use private networks and TLS for cross-host traffic; TLS termination is an operator responsibility.
Worker `/health` and `/ready` are public; capabilities, jobs and downloads require the key.
Worker-to-server heartbeats use the same worker-key header.

Generate independent secrets with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Production server settings require a unique non-placeholder worker key of at least 32 characters.
Keep keys out of logs, browser bundles and committed environment files. Coordinate updates across
services when rotating a shared key; there is no per-worker credential rotation API.

Application login uses `Authorization: Bearer <JWT>` with the separate JWT secret. Passwords use
PBKDF2-SHA256 hashes. Do not substitute user tokens for worker keys.

## Ownership and authorization

Modelryx authorizes users and owns Items, revisions, immutable snapshots, BOMs and publication.
Independent workers need neither database credentials nor access to the server's object volume.
Local preprocessors run on clients and need no worker key. Their registrations bind trusted
installed executables to worker IDs, CAD systems and formats; install the complete distribution.

Administrators configure worker URLs. Normal format discovery exposes identity/capabilities,
not credentials. Shared DWG/DXF extensions use the selected CAD-system filter. Ambiguous
registrations are errors rather than an arbitrary endpoint choice.

## Input and result validation

The [SDK](../../worker-sdk/README.md) defines hashes, byte limits, safe package paths, request
identity, cancellation and manifests. Use source media types to distinguish native ZIP-based
files from dependency archives. Bound expanded bytes/counts and reject traversal, links and
ambiguous paths. Process native copies in bounded workspaces and disposable processes.

The server checks result identity, requested/allowed roles, hashes, sizes, media types and schemas.
It downloads job-scoped outputs from the configured worker origin without redirects.
Partial success still requires every required output. Idempotent job replay is a lifecycle
feature, not cryptographic replay prevention. Do not publish unfinished outputs.

Liveness does not establish licensing or extraction accuracy; readiness checks vary by worker.
Qualify native fixtures and supported runtimes. Report missing data and ambiguous geometry mapping
rather than guessing. Propagate cancellation/timeouts to owned processes and retain bounded logs.

See [deployment](../operations/deployment.md),
[OpenAPI](../../worker-sdk/contract/openapi.yaml), [FreeCAD](../../workers/freecad/README.md) and
[security reporting](../../SECURITY_POLICY.md).
