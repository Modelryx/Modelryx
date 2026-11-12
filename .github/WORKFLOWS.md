# GitHub Workflows - Modelryx CI/CD

This document describes the GitHub Actions workflows used for Continuous Integration and automated testing in the Modelryx project.

## Workflow Overview

| Workflow | Trigger | Purpose | Status |
|----------|---------|---------|--------|
| **Platform CI** (`ci.yml`) | Push to main, Pull requests | Run all backend/frontend tests, validate Compose | ✅ Active |
| **Container Builds** (`container-builds.yml`) | Push to main, Pull requests | Build Docker images for all services | ✅ Active |
| **FreeCAD Compatibility** (`freecad-compatibility.yml`) | Manual workflow dispatch | Test FreeCAD worker against multiple versions | ✅ Active |

---

## Platform CI (`ci.yml`)

**Runs on:** Every push to `main` and all pull requests  
**Duration:** ~10-15 minutes

### Jobs

#### 1. Backend Tests
- **Runner:** Ubuntu 24.04
- **Steps:**
  - Checkout code
  - Run PostgreSQL database test suite using Docker Compose
  - Environment: `MODELRYX_INTERNAL_WORKER_KEY=ci-only-worker-key`
- **Purpose:** Validate backend API, database operations, and worker integration
- **Failure:** Blocks PR merge

#### 2. Frontend Tests
- **Runner:** Ubuntu 24.04
- **Working Directory:** `front-end/`
- **Steps:**
  - Checkout code
  - Setup Node.js 22 with npm caching
  - `npm ci` — Install dependencies
  - `npm run typecheck` — TypeScript type checking
  - `npm run build` — Vite production build
  - Install Playwright browsers
  - `npm run test:e2e` — End-to-end browser tests
- **Purpose:** Validate frontend build, TypeScript types, and E2E flows
- **Failure:** Blocks PR merge

#### 3. Worker SDK Tests
- **Runner:** Ubuntu 24.04
- **Steps:**
  - Setup Python 3.12
  - Install pytest and worker SDK
  - Run worker SDK conformance tests
- **Purpose:** Validate worker SDK implementation and API contracts
- **Failure:** Blocks PR merge

#### 4. FreeCAD Worker Tests
- **Runner:** Ubuntu 24.04
- **Steps:**
  - Setup Python 3.12
  - Install FreeCAD worker dependencies
  - Run pytest for FreeCAD worker
  - Build FreeCAD Docker image (`modelryx-freecad-ci`)
  - Run FreeCAD release gates (4 tests):
    - `golden_integration_test.py` — Validates metadata extraction
    - `dependency_integration_test.py` — Validates dependency detection
    - `production_fixture_test.py` — Tests production STEP files
    - `process_tree_integration_test.py` — Validates assembly structure
- **Purpose:** Ensure FreeCAD worker meets quality standards before release
- **Failure:** Blocks PR merge

#### 5. STEP Worker Tests
- **Runner:** Ubuntu 24.04
- **Steps:**
  - Setup Python 3.12
  - Install STEP worker dependencies
  - `python -m compileall` — Check Python syntax
  - Run pytest for STEP worker:
    - API contract tests
    - Component bundle tests
    - GLB utility tests
- **Purpose:** Validate STEP worker functionality and 3D model processing
- **Failure:** Blocks PR merge

#### 6. Repository Safety
- **Runner:** Ubuntu 24.04
- **Steps:**
  - Check for committed credentials (AWS, OpenAI, GitHub tokens, SSH keys)
  - Validate Docker Compose configuration
  - Environment: `MODELRYX_INTERNAL_WORKER_KEY=ci-only-worker-key`
- **Purpose:** Prevent accidental credential commits
- **Failure:** Blocks PR merge

---

## Container Builds (`container-builds.yml`)

**Runs on:** Every push to `main` and all pull requests  
**Duration:** ~5-10 minutes per image (parallelized)

### Matrix Build Strategy

Builds 4 Docker images in parallel:

| Image | Context | Dockerfile | Registry Tag |
|-------|---------|-----------|---------|
| server | `.` | `server/Dockerfile` | `modelryx/server:ci` |
| frontend | `front-end` | `front-end/Dockerfile` | `modelryx/frontend:ci` |
| freecad-worker | `.` | `workers/freecad/Dockerfile` | `modelryx/freecad-worker:ci` |
| step-worker | `workers/step` | `workers/step/Dockerfile` | `modelryx/step-worker:ci` |

### Build Cache
- **Cache From:** GitHub Actions cache (scope per service)
- **Cache To:** Store in GitHub Actions for future builds (max mode)
- **Speed Benefit:** 10-15x faster builds on subsequent runs

### Key Changes from PartForge
- ✅ Updated context path: `front` → `front-end`
- ✅ Updated registry prefix: `partforge/` → `modelryx/`

---

## FreeCAD Compatibility (`freecad-compatibility.yml`)

**Trigger:** Manual workflow dispatch  
**Duration:** ~20 minutes per FreeCAD version (sequential)

### Purpose
Validate FreeCAD worker compatibility across multiple FreeCAD releases. Allows testing against different versions without blocking main CI.

### Matrix Versions
- `1.0.0` — Legacy stable (warnings allowed)
- `1.1.3` — Current stable (required to pass)
- `2026.09.16` — Latest development (warnings allowed)

### Jobs

#### Native Fixtures Job
For each FreeCAD version:

1. **Build:** Docker image with specified FreeCAD version
   - Build arg: `MODELRYX_FREECAD_VERSION=${{ matrix.freecad_version }}`
   - Image tag: `modelryx-freecad-compat`

2. **Test:** Run 3 integration tests
   - `golden_integration_test.py` — Metadata extraction
   - `dependency_integration_test.py` — Dependency detection
   - `production_fixture_test.py` — Production files (compatibility mode)

### Continue on Error
- Version 1.1.3: Tests must pass (blocks release)
- Other versions: Can fail without blocking (informational)

### Key Changes from PartForge
- ✅ Updated build arg: `PARTFORGE_FREECAD_VERSION` → `MODELRYX_FREECAD_VERSION`
- ✅ Updated image name: `partforge-freecad-compat` → `modelryx-freecad-compat`
- ✅ Updated Python paths: `/opt/partforge-*` → `/opt/modelryx-*`

---

## Environment Variables

### Global CI Variables

| Variable | Usage | Example |
|----------|-------|---------|
| `MODELRYX_INTERNAL_WORKER_KEY` | Worker authentication in tests | `ci-only-worker-key` |

### Per-Workflow Variables

**ci.yml:**
- `MODELRYX_INTERNAL_WORKER_KEY` — Set in backend and validate-compose jobs

**container-builds.yml:**
- No special variables (uses context and file path)

**freecad-compatibility.yml:**
- `MODELRYX_FREECAD_VERSION` — Build arg for FreeCAD version matrix

---

## Secrets Management

No repository secrets are currently required for CI. All sensitive configuration:
- Uses `.env.example` for template
- Is set at deployment time (not committed)
- Database credentials only needed for integration tests (using Docker Compose test profile)

### For Custom Deployments
If you need to add private registries, package repositories, or deployment credentials:

1. Add secret in repository Settings → Secrets and variables → Actions
2. Reference in workflow: `${{ secrets.SECRET_NAME }}`

Example:
```yaml
env:
  REGISTRY_TOKEN: ${{ secrets.DOCKER_REGISTRY_TOKEN }}
```

---

## Failure Troubleshooting

### Backend Tests Fail
```bash
# Check PostgreSQL test database
docker compose --profile test run --rm test logs

# Run tests locally
cd server
python -m pytest -v
```

### Frontend Build Fails
```bash
# Clear npm cache and reinstall
cd front-end
rm -rf node_modules package-lock.json
npm install
npm run build
```

### FreeCAD Worker Tests Fail
```bash
# Run pytest locally
cd workers/freecad
pytest -v

# Check FreeCAD installation
docker build -f workers/freecad/Dockerfile .
docker run --rm <image-id> python /opt/modelryx-converter/golden_integration_test.py
```

### Container Build Fails
```bash
# Test build locally
docker build -f server/Dockerfile .

# Check cache issues
docker system prune -a
```

### Compose Validation Fails
```bash
# Validate locally
MODELRYX_INTERNAL_WORKER_KEY=test docker compose config

# Check for unset variables
grep '\$\|:?' compose.yaml
```

---

## Optimization Tips

### Reduce CI Duration

1. **Skip non-blocking tests:**
   - Use `if: ${{ github.event_name == 'push' }}` for expensive tests on PR
   - Or use `paths` filter to run jobs only when specific files change

2. **Cache optimization:**
   - Container builds use GitHub Actions cache (best for this setup)
   - Frontend npm cache already configured
   - Consider caching Python pip for worker tests

3. **Parallel execution:**
   - Container builds already parallelize across 4 images
   - Consider running frontend and backend in parallel (separate jobs)

### Example: Cache Python Dependency
```yaml
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"
    cache: 'pip'
- run: pip install -r requirements.txt
```

---

## Adding New Workflows

To add a new workflow (e.g., security scanning, nightly builds):

1. **Create** `.github/workflows/new-workflow.yml`
2. **Update** this documentation
3. **Add** to the workflow overview table above
4. **Test** by triggering manual workflow dispatch

Example skeleton:
```yaml
name: New Workflow
on:
  push:
    branches: [main]
  schedule:
    - cron: '0 2 * * *'  # Daily at 2 AM UTC

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - name: Run task
        run: echo "Hello from new workflow"
```

---

## Related Documentation

- [README.md](../README.md) — Project overview
- [DEVELOPMENT.md](../DEVELOPMENT.md) — Local development setup
- [DEPLOYMENT.md](docs/DEPLOYMENT.md) — Production deployment
- [.github/workflows/](.github/workflows/) — Workflow source files

---

**Last Updated:** 2026  
**Maintainer:** Modelryx Team  
**Status:** Open-source distribution
