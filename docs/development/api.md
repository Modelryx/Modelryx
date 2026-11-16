# Modelryx API reference

The running [Swagger UI](http://localhost:8000/docs) and
[OpenAPI schema](http://localhost:8000/openapi.json) define current methods, parameters and responses.
The application base URL is `http://localhost:8000/api/v1`. CAD worker services use a separate API
and authentication contract.

## Authentication

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<configured-bootstrap-password>"}'
```

Login accepts `username` and `password`; it is not an email-registration API. The response includes
`access_token`, `token_type`, `expires_in`, `display_name`, `role` and `roles`.

```bash
curl http://localhost:8000/api/v1/session \
  -H 'Authorization: Bearer <access_token>'
```

There is no public `/auth/register` or refresh-cookie endpoint. Administrators manage users/roles.
Authorization is enforced on the server. Not every endpoint uses JSON: follow the declared
content types for native uploads, resumable chunks and downloads.

## Route families

Inspect the OpenAPI tags and modules under `server/src/server/modelryx/api` for exact contracts:

| Area | Source modules |
| --- | --- |
| Login/session | `auth.py`, `main.py` |
| Items/revisions | `items.py`, `parts.py`, `previews.py`, `revision_comparisons.py` |
| Files/snapshots | `artifacts.py`, `artifact_commits.py`, `attachments.py` |
| Staging/validation | `staging.py`, `staging_workflows.py`, `validation.py` |
| Managed structures | `assemblies.py`, `relationships.py`, `bom_analysis.py` |
| Checkout/approvals | `checkouts.py`, `revision_approvals.py`, `approval_groups.py`, `approval_delegations.py` |
| Search/AI | `search.py`, `engineering_search.py`, `ai.py`, `assistant.py`, `knowledge.py` |
| Personal state/views | `personal.py`, `ui_views.py` |
| Administration | `admin.py`, `operations.py`, `worker_health.py` |
| Native CAD sessions | `cad.py`, `actions.py` |

The frontend Workspace label does not imply a generic `/workspaces` CRUD API. Revision content
uses immutable snapshots and controlled staging, validation and commit workflows. Item numbers,
source lineage and managed BOM identity are server-owned.

## Worker selection and availability

- `GET /api/v1/workers/external-formats`: enabled format/identity discovery without credentials.
- `GET /api/v1/workers/preflight?worker_type=FREECAD`: availability of the selected FreeCAD path,
  including external registration, dedicated standalone URL and embedded heartbeat fallback.
- `GET /api/v1/admin/external-workers`: administrator-managed registrations.
- `GET /api/v1/admin/external-workers/status`: registered-worker readiness/capability checks.
- `GET /api/v1/admin/cad-workers/status`: dedicated endpoint status, including FreeCAD worker/analyzer URLs.

Independent workers implement the [SDK](../../worker-sdk/README.md), with multipart files named
`request`/`source`, scoped output manifests and `X-Modelryx-Worker-Key`. Client preprocessors use
a separate executable/report contract. Workers do not write Modelryx engineering records.

## Health and errors

`GET /health/live` checks API liveness. `GET /health/ready` checks database connectivity.
`GET /health` is broader health information; none proves every native CAD runtime is ready.
FastAPI errors generally use `detail`, which may be structured for validation failures. Do not
assume an invented common error envelope or identical status codes across endpoints.

Use the running schema and `server/tests` as examples. [Testing](testing.md) describes isolated
fixtures; do not execute destructive examples against production. Schema initialization is an
[operator command](../operations/database-initialization.md), not an API migration endpoint.
