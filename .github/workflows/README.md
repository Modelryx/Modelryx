# Workflow layout

- `ci.yml` runs required backend schema initialization/tests, frontend compilation, SDK tests,
  FreeCAD unit/native tests, STEP contract tests, and repository checks. Backend
  coverage and the Trivy security report are uploaded as artifacts.
  The STEP job also builds its image once with Buildx caching, runs native converter tests,
  and runs an authenticated service smoke test. Backend Compose tests build the API runtime
  and test images. FreeCAD is built and tested once using the native Docker builder.
- `freecad-compatibility.yml` is manually dispatched for the FreeCAD version matrix.
  Failures for versions other than the supported production version are advisory.

Automatic workflows run on all pull requests and pushes to `main` and `develop`.
Superseded runs for the same workflow/ref are cancelled. Jobs have explicit timeouts.

## Quality reports

Full Ruff, ESLint, and frontend formatting checks are required. Existing lint
errors and formatting violations have been resolved. ESLint warnings remain visible
without failing the job. Trivy fails for fixable HIGH or CRITICAL findings, and
scanner failures also fail the job. SARIF is retained as an artifact so reports work
without GitHub Code Security or write permissions on fork pull requests. SARIF
severity filtering is explicitly enabled, and matching rule IDs and locations are
printed in the job log.

Worker unit tests use Python 3.11 to match the native worker images. Server and SDK
checks use Python 3.12, and frontend checks use Node 22. Frontend CI installs the
committed lockfile with `npm ci`, without ad hoc package installs.
When updating dependencies, generate the lockfile in a clean directory without an
existing lockfile or node_modules and verify Linux glibc, musl, and Windows entries.

Native FreeCAD jobs free unused preinstalled runner SDKs before building. Backend
coverage data and XML reports are written under `/tmp`, which is writable by the
non-root test user. Coverage is required after passing tests; when tests fail before coverage is
written, collection warns and preserves the original test failure.

Frontend CI runs `npm run test:upload` for the neutral upload contract. It does not
install Playwright or run browser tests.

## Required checks

Select required checks from Platform CI. The `STEP worker` job includes native image and
service checks, and the `FreeCAD worker` job includes native release gates. Remove any obsolete
container matrix checks from branch protection after this workflow change is applied.
