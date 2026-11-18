# Deploying Modelryx

Use [compose.yaml](../../compose.yaml) as the actual service definition. The frontend is hosted
separately; additional CAD workers are independently deployed services. See [development](../../DEVELOPMENT.md)
for local setup.

## Environment and data

Copy `.env.example` to `.env` and replace placeholders with independent secrets. Configure
`POSTGRES_PASSWORD`, `MODELRYX_JWT_SECRET`, `MODELRYX_INTERNAL_WORKER_KEY`, bootstrap username and
`MODELRYX_BOOTSTRAP_ADMIN_PASSWORD`. Production requires `MODELRYX_ENVIRONMENT=production`, unique
non-placeholder JWT/worker keys of at least 32 characters and an administrator password of at
least 16 characters. Set exact allowed frontend origins using `MODELRYX_CORS_ORIGINS` JSON.

`postgres-data` contains metadata; `object-data` contains native and derived files. API and general orchestrator share `/var/lib/modelryx/objects`. STEP/external job volumes retain conversion
jobs according to worker policy. Independent workers need neither database credentials nor
object-volume access. Local storage is the application backend; optional S3 utilities do not
switch uploads/downloads to S3.

Validate without printing resolved secrets:

```powershell
docker compose config --quiet
```

## Select services

Default startup includes API, database/storage initialization, general orchestrator and STEP:

```powershell
docker compose up -d --build
```

Deploy FreeCAD separately using [the FreeCAD launcher](../../workers/freecad/README.md#standalone-http-service),
then register its URL in **External CAD Workers**. Docker Desktop can reach a host worker at
`http://host.docker.internal:8090` when started with `-ListenAddress 0.0.0.0`. Other hosts require
a private reachable address/TLS endpoint. Starting a service does not register it.

FreeCAD requires an enabled matching registration or configured external URL. Controlled CAD
editing sessions are disabled. Open CAD File requires a separately installed preprocessor on
each client; all extraction runs on the worker server.

## Frontend and networking

In `front-end/`, run `npm ci` and `npm run build`; serve `dist/` with SPA fallback to `index.html`.
Vite port 5173 is the development server, not production hosting. Users enter the API URL in the
login form. Configure the frontend's exact CORS origin and the Local CAD Agent's allowed origins.

Terminate HTTPS at an ingress/reverse proxy to API port 8000. Worker authentication is the shared
`X-Modelryx-Worker-Key` header, not signed requests. Keep workers private and use TLS across hosts.
Compose binds STEP on 8080 and API on 8000; apply suitable host/network restrictions. PostgreSQL
binds to loopback by default. See [worker security](../security/worker-security.md).

## Updates and verification

`initialize-db` creates a fresh schema or verifies a matching fingerprint. It does not upgrade
an incompatible schema. Back up and qualify schema changes on isolated copies; do not delete
volumes to bypass initialization errors. See [initialization](database-initialization.md).

Rebuild changed API/general-worker code with `docker compose up -d --build api general-worker`.
Recreate containers for environment-only changes; a plain restart does not reload Compose values.
Rebuild workers separately, re-register changed capabilities and update client preprocessors
separately from server DLLs/images.

Check `/health/live`, `/health/ready`, authenticated sign-in, source downloads and representative
previews. Inspect `docker compose ps` and logs. Operations shows configured worker statuses;
FreeCAD upload preflight checks the configured external route. Native qualification is
separate from liveness and cannot be inferred from an ONLINE card alone.

## Operational limitations and monitoring

Health probes are configured for the service role in Compose: the API and database
use their readiness checks, and standalone FreeCAD has its own probe. The shared
API/worker images do not define one generic Dockerfile `HEALTHCHECK`, because the
same image can run a long-lived API, a processing worker, or a one-shot command.
Other orchestrators must configure equivalent role-specific supervision and
worker-heartbeat monitoring; a running process alone is not a readiness guarantee.

The first published version does not include built-in backup or restore support.
Full backup and recovery support may be added in a future release.
`docker compose down -v` deletes volumes and is not a normal restart. Monitor capacity, queues, errors and retained-job disk use. Read
worker/SDK limitations before promising publication of every optional native output role.
