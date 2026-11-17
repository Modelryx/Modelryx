# Server development

Use [the root development guide](../DEVELOPMENT.md) for setup. Run Compose from the repository
root and the frontend from `front-end/`. The UI uses **5173** with strict port checking;
the API uses **8000**. Enter the reachable API address and valid credentials in the sign-in form.

For host Python development, install the SDK and server editable packages, configure the host
PostgreSQL URL and storage root in `server/.env`, then run from `server/`:

```powershell
python -m modelryx.initialize_database
python -m uvicorn modelryx.api.main:app --reload --port 8000
```

Initialization creates a fresh schema or verifies a matching one. There is no automatic
migration command. Built-in backup and restore support is not included in the first published version.

For Docker-backed changes, run from the repository root:

```powershell
docker compose up -d --build api general-worker
```

For environment-only changes, recreate the affected containers rather than restarting them.
On an initialized deployment use `docker compose up -d --no-deps api general-worker`.
Allow exact frontend origins through `MODELRYX_CORS_ORIGINS`, e.g.
`["http://localhost:5173","http://192.168.1.20:5173"]`. Browser localhost is the client machine.

See [testing](../docs/development/testing.md), [API](../docs/development/api.md),
[worker protocol](../worker-sdk/README.md).
The frontend has typecheck/build/lint scripts, not an `npm test` script.
