# GitHub Actions

The workflow files are the authoritative definitions of CI behavior. Branch protection and
required checks are repository settings; a workflow failure alone does not configure them.

## Platform CI

[ci.yml](workflows/ci.yml) runs for pull requests and pushes to `main` and `develop`:

- Backend API and database tests run in an isolated Compose test database.
- Frontend checks cover the lockfile, TypeScript, API client and neutral upload contracts, production build,
  ESLint, and Prettier.
- Worker SDK tests cover the protocol, configuration, license provenance, and change classification.
- STEP checks cover API, component-bundle, and GLB contracts, a cached container build,
  native converter tests, and an authenticated running-service smoke test.
- Repository safety checks scan common credential patterns and validate Compose.
- Python quality runs Ruff; dependency/configuration scanning uses Trivy and retains SARIF.

Backend Compose tests build the API runtime and test images. The STEP image is built and
loaded once by the STEP worker job for its native and service checks. Images are not pushed
to a registry.

## Documentation-only changes

Platform CI still starts for each matching event so required checks are reported.
A lightweight `Classify changes` job compares the entire push range or the PR
merge-base-to-head range. For documentation-only changes, it skips the backend,
frontend, SDK, STEP, Python-quality and Trivy jobs. Their existing check
names are unchanged; GitHub accepts conditionally skipped jobs for required-check
purposes. The repository-safety job always runs, including the credential-pattern
check and Compose validation, because documentation can contain leaked secrets.

Safe-to-skip changes are Markdown files except `server/README.md` and
`worker-sdk/README.md`, plus static image/PDF/reStructuredText files under `docs/`.
Those two READMEs are image/package inputs and still require CI. Code, tests,
workflows, Compose/environment settings, `.gitattributes`, license texts/manifests,
and unknown file types run full CI. Mixed documentation/platform-code changes also run it. FreeCAD-only changes skip platform build
and test jobs because the independent worker workflow owns them; repository safety still runs.
Missing comparison history, new branches, or classifier errors default to full CI.
Rename comparisons include both old and new paths.

Do not replace this with workflow-level `paths-ignore` on required workflows:
[GitHub leaves checks pending when the whole workflow is filtered out](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs).
FreeCAD CI is path-filtered and must not be a globally required check for unrelated pull requests.
Keep Platform CI checks required globally; require native worker checks through worker-specific
review policy where supported.
The manually dispatched FreeCAD compatibility workflow is unaffected.

## FreeCAD CI

[freecad.yml](workflows/freecad.yml) runs only for non-Markdown changes under
`workers/freecad/` or `worker-sdk/` on pull requests and pushes to `main`/`develop`, plus manual dispatch.
Changes in `server/`, `front-end/`, root scripts, or workflow files alone do
not trigger it. After changing the workflow itself, use manual dispatch to validate it.

The Docker context is the worker directory. Runtime and native tests use worker-owned versioned
SDK/schema snapshots and do not install or import the platform. Worker dependency/configuration
security scanning also runs in this workflow. Snapshot updates are explicit
worker changes and therefore trigger native CI. See the [worker guide](../workers/freecad/README.md).

## FreeCAD compatibility

[freecad-compatibility.yml](workflows/freecad-compatibility.yml) provides manually dispatched
native compatibility checks. Read its inputs and matrix before starting a run.

External CAD implementations own their native SDK builds, runtime qualification, and CI in
their separate repositories. Platform CI validates the generic contract with synthetic data.

See the [testing guide](../docs/development/testing.md),
[deployment guide](../docs/operations/deployment.md), and [workflow directory](workflows/).
