# Testing Modelryx

Commands run from the repository root unless indicated otherwise. CI's implemented checks are
in `.github/workflows/ci.yml`; this guide does not imply enforced coverage percentages or a
frontend test framework that is not installed.

## Isolated backend tests

Tests require `MODELRYX_ENVIRONMENT=test` and a database named `modelryx_test`. Fixtures refuse
other databases. Use a separate Compose project so reset/init services cannot affect application
services:

```powershell
docker compose -p modelryx-check --env-file .env.example --profile test run --build --rm test pytest -q
```

This initializes `db-test`, intentionally resets that project's test schema, then executes the
test image. Never point these services at a live database. A different test-only environment
file can replace `.env.example`; example secrets are not production values. For a targeted run,
append paths such as `tests/test_freecad_worker_routing.py` rather than the full suite.

```powershell
docker compose -p modelryx-check --env-file .env.example --profile test down
```

The application database is `modelryx`; the isolated test database is `modelryx_test`.
Tests cover auth, artifacts/snapshots, staging, lifecycle, managed BOMs, publication, workers,
operations and optional AI/search boundaries. Read `server/tests/conftest.py` before running
outside Docker. Install `worker-sdk` and `server[dev]` and configure a dedicated PostgreSQL/pgvector
test database for host tests; SQLite is not a substitute for these integration tests.

## SDK and worker unit tests

```powershell
python -m pip install './worker-sdk[conformance]' pytest -r workers/freecad/standalone/requirements.txt
python -m pytest -q worker-sdk/tests
cd workers/freecad
python -m pytest -q tests
```

FreeCAD's pytest configuration supplies its converter/import paths. Unit tests use fakes and
fixtures rather than requiring native FreeCAD. Use a short pytest temporary path on Windows
when nested paths exceed filesystem limits. Golden, dependency and production native fixtures
run separately in the FreeCAD runtime; see [the worker guide](../../workers/freecad/README.md).

From `workers/step/`, install pytest, FastAPI, HTTPX, python-multipart, NumPy and trimesh, then:

```powershell
python -m pytest -q tests/test_api_contract.py tests/test_component_bundle_api.py tests/test_component_bundle_units.py tests/test_glb_utils.py
```

Native OpenCascade checks require its image; see [STEP verification](../../workers/step/README.md#verification).

## Frontend and Local CAD Agent

From `front-end/`:

```powershell
npm ci
npm run check:lock
npm run typecheck
npm run build
npm run lint
npm run format:check
```

There is no `npm test` script. Some CI lint/format checks are informational; inspect the workflow
before describing them as release gates. Native dialogs and browser workflows require separate
end-to-end/manual qualification.

The agent uses executable .NET harnesses, not `dotnet test`:

```powershell
dotnet build local_cad_agent/tests/Modelryx.LocalCad.Agent.Tests -c Release -m:1
dotnet local_cad_agent/tests/Modelryx.LocalCad.Agent.Tests/bin/Release/net8.0/Modelryx.LocalCad.Agent.Tests.dll
```

These check CAD-system routing and package/native integration seams. Real vendor SDK and licensing
checks remain separate.

## External qualification

Use the [SDK probe](../../worker-sdk/README.md#sdk-development) on a disposable real source.
It creates a real worker job and checks auth, capabilities, multipart submission, replay/conflicts,
status, output hashes and terminal cancellation. Separately qualify active cancellation, restart
recovery, native accuracy, source immutability, nested/repeated references and managed BOM review.

Useful server regressions include `test_external_workers.py`, `test_freecad_worker_routing.py`,
`test_admin_cad_workers.py` and `test_operations.py`. A working worker API does not prove the server
accepts every advertised optional output role.


## Neutral native-upload contract

`npm --prefix front-end run test:upload` checks upload proposals using fictional CAD formats.
It verifies managed Item identities, occurrence names, drawing Artifacts, and package support
files without a native SDK or browser runtime. The local agent has its own SDK-free checks:

```powershell
dotnet run --project local_cad_agent/tests/Modelryx.LocalCad.Agent.Tests
./local_cad_agent/tests/Test-WindowsInstaller.ps1
```

External adapter implementations and native application qualification belong in the worker's
repository. The platform tests the shared protocol and artifact publication boundary.


## Frontend API client contract

`npm --prefix front-end run test:api` checks that authenticated requests stay under the
configured API URL, JSON payloads retain falsy values, and expired sessions clear credentials
and notify the sign-in flow. These checks run in Platform CI alongside the upload contract.
