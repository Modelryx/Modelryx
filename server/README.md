# Modelryx server

FastAPI API and database-backed orchestration for Items, revisions, immutable content snapshots,
artifacts, managed BOMs, checkout, approvals, search and optional AI features. The frontend is
in `../front-end`; native conversion code is in `../workers` or separate worker checkouts.

## Startup

Follow [the root development guide](../DEVELOPMENT.md). Configure `.env` and run Compose from
the repository root. Run `npm ci` and `npm run dev` separately from `front-end/`.

- Frontend: [localhost:5174](http://localhost:5174)
- API: [localhost:8000](http://localhost:8000)
- API reference: [docs](http://localhost:8000/docs)
- Schema: [openapi.json](http://localhost:8000/openapi.json)

The Python entry point is `modelryx.api.main:app`. Install `worker-sdk` before `server[dev]`,
configure `server/.env` for the host database/storage, initialize and run Uvicorn from `server/`
when using a host Python environment.

## Authentication and persistence

`POST /api/v1/auth/login` accepts `username` and `password`. Authenticated application calls
use `Authorization: Bearer <access_token>`. Use configured bootstrap credentials, not an
assumed default password. Administrators manage users; no public registration or refresh-token
endpoint is implemented.

Compose retains database metadata in `postgres-data` and files in `object-data`, mounted at
`/var/lib/modelryx/objects`. Back up both together. Application file workflows use local storage;
the optional S3 helper is not a selectable storage backend. `python -m modelryx.initialize_database`
creates a fresh schema or verifies a matching one; it does not migrate/reset an incompatible
database. See [initialization](../docs/operations/database-initialization.md).

## Workers

`general-worker` handles generic dispatch, staging/STEP and external CAD jobs. `freecad-worker`
provides embedded FCStd processing and FreeCAD CAD sessions. Enabled matching external registration
overrides FreeCAD conversion; absent/disabled registration retains the dedicated-URL/embedded
path. STEP retains its built-in service path. Independent HTTP workers receive no database
credentials or object-volume mount. See [the SDK](../worker-sdk/README.md).

## Tests and layout

Use [isolated tests](../docs/development/testing.md). Backend tests refuse a non-test environment
or a database not named `modelryx_test`.

- `src/server/modelryx/api`: application and routes
- `src/server/modelryx/core`: configuration, auth, auditing and database setup
- `src/server/modelryx/models`: mapped entities and request/response schemas
- `src/server/modelryx/services`: jobs, publication, storage and workflows
- `tests`: backend tests
- `../front-end/src/viewer`: browser viewer
- `../docs/ai/overview.md`: AI/search configuration and behavior
