# Modelryx

CAD-neutral part and assembly management backend with a browser frontend and 3D viewer.
It supports resumable source check-ins,
immutable working versions, checkout locks, grouped multi-file assembly ingestion, SHA-256
component reuse, immutable nested BOM occurrences, where-used queries, and asynchronous
FreeCAD preview processing.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for the recommended development setup.

Docker Compose runs separate `general-worker` and `freecad-worker` services. The general
worker handles staging, STEP, AutoCAD, Inventor, and assembly-bundle dispatch without a
FreeCAD runtime. The FreeCAD image claims only FCStd conversions and FreeCAD-targeted CAD
sessions. Both share the PostgreSQL `ProcessingJob` queue and object storage.

**Quick start:**
```powershell
# Start backend services
docker compose up -d

# In a separate terminal, start frontend
cd ..\front
npm install
npm run dev
```

Frontend: `http://localhost:5173` | API: `http://localhost:8000` | Docs: `http://localhost:8000/docs`

## Authentication

Sign in with the bootstrap administrator to receive a short-lived JWT. The development
defaults are username `admin` and password `admin`; set a strong password and JWT secret
before deploying.

User management prevents administrators from disabling or demoting themselves and
requires at least one active administrator to remain. Access changes are serialized
in PostgreSQL and the acting administrator is revalidated after acquiring the lock,
so concurrent changes cannot bypass the protection.

Production deployments must set `MODELRYX_ENVIRONMENT=production`, a unique JWT secret of
at least 32 characters, a strong bootstrap password, and the exact permitted CORS origins.
Startup fails if insecure authentication defaults are used in production mode.

```powershell
$login = Invoke-RestMethod http://localhost:8000/api/v1/auth/login -Method Post `
  -ContentType "application/json" -Body '{"username":"admin","password":"admin"}'
Invoke-RestMethod http://localhost:8000/api/v1/session `
  -Headers @{ Authorization = "Bearer $($login.access_token)" }
```

Administrators have an **Administration** area in the browser for creating and disabling
users, assigning roles, resetting passwords, archiving/restoring items, permanently purging an
archived test item and its files, checking operational health, and reviewing recent audit activity.
Permanent purge requires the exact item number and is blocked while another item's assembly or
revision relationship references the target.

## Testing

```powershell
docker compose run --rm test
```

## Source layout

- `src/server/modelryx/api`: FastAPI application and route modules
- `src/server/modelryx/core`: configuration, authentication, auditing, and database setup
- `src/server/modelryx/models`: SQLAlchemy entities and Pydantic schemas
- `src/server/modelryx/services`: processing, storage, and reconciliation services
- `../docs/ai/overview.md`: pgvector RAG, gateway configuration, indexing, and query APIs
- `../front`: React frontend (runs separately in development)
- `src/viewer`: TypeScript/Vite 3D viewer source
- `../workers/freecad`: FreeCAD conversion runtime and optional command-line tools
- `../workers/step`: shared neutral STEP processing service
- `tests`: backend tests

## Database initialization

Compose initializes an empty PostgreSQL/pgvector database with `initialize-db`
before starting the API and workers. For a local Python installation, run
`python -m modelryx.initialize_database` from `server/` after configuring the database
URL. The command creates the current schema directly, seeds a paired Part/Assembly
numbering template, and safely verifies repeat runs. It never upgrades or resets an
existing incompatible schema. See [database initialization](../docs/operations/database-initialization.md).
