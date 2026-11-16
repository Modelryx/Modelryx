# GitHub Actions

The workflow files are the authoritative definitions of CI behavior. Branch protection and
required checks are repository settings; a workflow failure alone does not configure them.

## Platform CI

[ci.yml](workflows/ci.yml) runs for pull requests and pushes to `main` and `develop`:

- Backend API and database tests run in an isolated Compose test database.
- Frontend checks cover the lockfile, TypeScript, API client and neutral upload contracts, production build,
  ESLint, and Prettier.
- Worker SDK tests cover the protocol and repository configuration/export checks.
- FreeCAD checks cover unit tests, a native image build, and native release gates.
- STEP checks cover API, component-bundle, and GLB contracts, a cached container build,
  native converter tests, and an authenticated running-service smoke test.
- Repository safety checks scan common credential patterns and validate Compose.
- Python quality runs Ruff; dependency/configuration scanning uses Trivy and retains SARIF.

Backend Compose tests build the API runtime and test images. The STEP image is built and
loaded once by the STEP worker job for its native and service checks. Images are not pushed
to a registry.

## FreeCAD compatibility

[freecad-compatibility.yml](workflows/freecad-compatibility.yml) provides manually dispatched
native compatibility checks. Read its inputs and matrix before starting a run.

External CAD implementations own their native SDK builds, runtime qualification, and CI in
their separate repositories. Platform CI validates the generic contract with synthetic data.

See the [testing guide](../docs/development/testing.md),
[deployment guide](../docs/operations/deployment.md), and [workflow directory](workflows/).
