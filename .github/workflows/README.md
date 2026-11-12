# Workflow layout

- `ci.yml` runs required backend migrations/tests, frontend compilation, SDK tests,
  FreeCAD unit/native tests, STEP contract tests, and repository checks. Backend
  coverage and the Trivy security report are uploaded as artifacts.
- `container-builds.yml` runs independently of CI. Its matrix builds server,
  frontend, and STEP images in parallel. The STEP image also runs native converter
  tests and an authenticated service smoke test. FreeCAD is built and tested once,
  in CI, with its own image cache.
- `freecad-compatibility.yml` is manually dispatched for the FreeCAD version matrix.
  Failures for versions other than the supported production version are advisory.

Automatic workflows run on all pull requests and pushes to `main` and `develop`.
Superseded runs for the same workflow/ref are cancelled. Jobs have explicit timeouts.

## Quality reports

Full Ruff, ESLint, and frontend formatting checks are required. Existing lint
errors and formatting violations have been resolved. ESLint warnings remain visible
without failing the job. Trivy fails for fixable HIGH or CRITICAL findings, and
scanner failures also fail the job. SARIF is retained as an artifact so reports work
without GitHub Code Security or write permissions on fork pull requests.

Worker unit tests use Python 3.11 to match the native worker images. Server and SDK
checks use Python 3.12, and frontend checks use Node 22. Frontend CI and container
builds install the committed lockfile with `npm ci`, without ad hoc package installs.

There is currently no frontend unit or browser test script. CI does not install
Playwright or invoke nonexistent test scripts. Add required test steps when a real
test suite is introduced.

## Required checks

This replaces `tests.yml` and `code-quality.yml`. Repository administrators should
remove their old check names from branch protection and select checks from Platform
CI and Container Builds. Keep the existing CI job IDs where possible. The removed
FreeCAD container matrix check is covered by the `FreeCAD worker` CI job.
