# Modelryx development setup

Commands run from the repository root unless stated otherwise. Use Docker Compose v2,
Node.js 22.12+ for Vite 7, and Python 3.11+ for host Python development. Compose supplies
PostgreSQL 17 with pgvector. Additional CAD formats are provided by independently deployed external workers.

## Environment setup

Copy `.env.example` to `.env` and replace its placeholders. Generate independent secrets:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set `POSTGRES_PASSWORD`, `MODELRYX_JWT_SECRET`, `MODELRYX_INTERNAL_WORKER_KEY` and
`MODELRYX_BOOTSTRAP_ADMIN_PASSWORD`. The example is a template, not a production configuration.
Production rejects insecure authentication defaults; see [deployment](docs/operations/deployment.md).
Do not commit real environment files.

## Recommended split setup

```powershell
docker compose up -d --build
```

Default services include `db`, `initialize-db`, `storage-init`, `api`, `general-worker`,
`step-worker`, `freecad-worker` and `freecad-preprocessor`. Initialization creates a fresh schema
or verifies a matching schema; it does not upgrade an incompatible database.
Start the frontend in another terminal:

```powershell
cd front-end
npm ci
npm run dev
```

Open [localhost:5173](http://localhost:5173), enter `http://localhost:8000` as the API address,
and sign in using configured credentials. Vite uses port 5173 with strict port checking.
It does not silently choose another port. The frontend runs outside Docker.

Rebuild changed API/general-worker code from the repository root:

```powershell
docker compose up -d --build api general-worker
```

For environment-only changes on an initialized deployment, recreate affected services with
`docker compose up -d --no-deps api general-worker`. A plain restart does not apply changed
Compose environment variables. Do not skip initialization on a fresh deployment.

## Run without embedded FreeCAD

```powershell
docker compose stop freecad-worker freecad-preprocessor
docker compose up -d --build api general-worker step-worker
.\workers\freecad\scripts\start-freecad-server.ps1 -ListenAddress 0.0.0.0
```

In Docker Desktop, register `http://host.docker.internal:8090` in **Administrator > External CAD
Workers**, using the shared internal key. An enabled matching registration overrides new FreeCAD
conversion jobs. Without one, the dedicated FreeCAD URL is used when configured, otherwise the
embedded role handles processing. There is no automatic embedded fallback after an external
job fails. CAD sessions still require the embedded runtime. Plain `docker compose up -d` starts
the default embedded services again. See [FreeCAD](workers/freecad/README.md) and
[worker SDK](worker-sdk/README.md).

## Host Python API

Start only PostgreSQL with `docker compose up -d db`. Create and activate a Python virtual
environment, then install from the repository root:

```powershell
python -m pip install -e ./worker-sdk
python -m pip install -e './server[dev]'
```

Copy `server/.env.example` to `server/.env`. Configure `MODELRYX_DATABASE_URL` using the same
Compose PostgreSQL credentials, database `modelryx`, and mapped host `localhost` port
(default 5432). Docker hostname `db` is not the host connection address. Configure a writable
storage root, authentication secrets and exact permitted frontend origins.
Settings load `.env` relative to the working directory; run from `server/`:

```powershell
cd server
python -m modelryx.initialize_database
python -m uvicorn modelryx.api.main:app --reload --host 0.0.0.0 --port 8000
```

There is no server `requirements.txt` installation path or `src.server.main:app` entry point.
Avoid host/container APIs competing for one port or using inconsistent storage roots. A direct
API process does not start conversion workers; configure the general/native workers separately.

## Verification and troubleshooting

See [testing](docs/development/testing.md) for isolated PostgreSQL tests, worker checks and
frontend commands. Inspect `docker compose ps`, `docker compose logs -f api`,
`docker compose logs -f general-worker`, and `docker compose logs -f db`.
API liveness is `/health/live`; database readiness is `/health/ready`.

The frontend chooses its API in the sign-in form, not a required `VITE_API_URL` variable.
For LAN use, enter the server's reachable address and add the exact frontend origin to
`MODELRYX_CORS_ORIGINS`, e.g. `http://192.168.1.20:5173`. The Local CAD Agent separately checks
allowed browser origins; see its [guide](local_cad_agent/README.md).

Do not use `docker compose down -v` as troubleshooting: it deletes persistent volumes.
The first published version does not include built-in backup or restore support.


## External worker boundary

Only FreeCAD and STEP have built-in processing paths. Additional native CAD formats require
an enabled registration under **Administrator > External CAD Workers**. The platform matches
CAD system and source extension against advertised capabilities; ambiguous registrations fail
instead of selecting a fallback. Worker implementations, native SDKs, installers, and runtime
qualification belong in their own repositories.

The Local CAD Agent requires a matching external preprocessor registration. Native runtime
discovery and installation are responsibilities of the external preprocessor.
