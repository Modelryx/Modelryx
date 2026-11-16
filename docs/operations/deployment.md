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

`postgres-data` contains metadata; `object-data` contains native and derived files. API/general/
embedded workers share `/var/lib/modelryx/objects`. STEP/external job volumes retain conversion
jobs according to worker policy. Independent workers need neither database credentials nor
object-volume access. Local storage is the application backend; optional S3 utilities do not
switch uploads/downloads to S3.

Validate without printing resolved secrets:

```powershell
docker compose config --quiet
```

## Select services

Default startup includes API, database/storage initialization, general and STEP workers,
embedded FreeCAD and the dependency preprocessor:

```powershell
docker compose up -d --build
```

For external FreeCAD without the embedded services:

```powershell
docker compose stop freecad-worker freecad-preprocessor
docker compose up -d --build api general-worker step-worker
```

Use [the FreeCAD launcher](../../workers/freecad/README.md#standalone-http-service), then register
its URL in **External CAD Workers**. Docker Desktop can reach a host worker at
`http://host.docker.internal:8090` when started with `-ListenAddress 0.0.0.0`. Other hosts require
a private reachable address/TLS endpoint; container localhost is not the worker's host.
Starting a service does not register it.

For `.FCStd`, one enabled matching registration takes precedence. Without one, the dedicated
FreeCAD worker URL applies if configured, otherwise the embedded role handles conversion.
External failure does not automatically retry through embedded FreeCAD. CAD sessions retain
the embedded runtime. STEP retains its built-in service path. 
## Frontend and networking

In `front-end/`, run `npm ci` and `npm run build`; serve `dist/` with SPA fallback to `index.html`.
Vite port 5174 is the development server, not production hosting. Users enter the API URL in the
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
FreeCAD upload preflight follows the selected remote/embedded route. Native qualification is
separate from liveness and cannot be inferred from an ONLINE card alone.

## Backup and monitoring

Follow [matched database/object backups](backup-strategy.md), retain source/configuration versions
and qualify isolated restores. Store secrets separately. `docker compose down -v` deletes volumes
and is not a normal restart. Monitor capacity, queues, errors and retained-job disk use. Read
worker/SDK limitations before promising publication of every optional native output role.
