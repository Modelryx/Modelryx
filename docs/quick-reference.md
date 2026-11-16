# Modelryx quick reference

Commands run from the repository root. Configure `.env` with real secrets first;
see [development](../DEVELOPMENT.md).

## Start and inspect

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f api
docker compose logs -f general-worker
docker compose logs -f db
```

Run `npm ci` then `npm run dev` separately from `front-end/`.
Open [localhost:5174](http://localhost:5174) and enter the API address and configured credentials.
API checks: [liveness](http://localhost:8000/health/live),
[readiness](http://localhost:8000/health/ready), [OpenAPI](http://localhost:8000/docs).

## External FreeCAD only

```powershell
docker compose stop freecad-worker freecad-preprocessor
docker compose up -d --build api general-worker step-worker
.\workers\freecad\scripts\start-freecad-server.ps1 -ListenAddress 0.0.0.0
```

In Docker Desktop, register `http://host.docker.internal:8090` under **External CAD Workers**.
Starting a worker alone does not register it. Without a matching enabled registration, FreeCAD
uses its dedicated URL or embedded role. External failures do not trigger embedded retries.
FreeCAD CAD sessions still require the embedded role.

## Verification

```powershell
python -m pytest -q worker-sdk/tests
cd front-end
npm run typecheck
npm run build
npm run lint
```

See [testing](development/testing.md) for isolated database tests and native qualification.
There is no frontend `npm test` command.

## Operations

- Apply API code: `docker compose up -d --build api`.
- Apply Compose environment changes: recreate affected services with `docker compose up -d`.
- Rebuild STEP: `docker compose up -d --build step-worker`.
- External FreeCAD: [worker README](../workers/freecad/README.md).
- Native selection/preprocessors: [agent README](../local_cad_agent/README.md).
- REST API: [reference](development/api.md).
- Fresh database: [initialization](operations/database-initialization.md).
- Backup: [database and objects](operations/backup-strategy.md).
- Optional S3 utilities: [scope](development/s3-operations.md).

`docker compose down -v` deletes persistent volumes; it is not a normal restart command.
