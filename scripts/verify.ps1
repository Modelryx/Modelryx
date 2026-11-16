$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$env:MODELRYX_INTERNAL_WORKER_KEY = "local-validation-key"
docker compose -f "$Root\compose.yaml" --profile test run --rm test
if ($LASTEXITCODE) { throw "Backend tests failed" }
Push-Location "$Root\front-end"
try {
    npm run typecheck; if ($LASTEXITCODE) { throw "Frontend type-check failed" }
    npm run build; if ($LASTEXITCODE) { throw "Frontend build failed" }
} finally { Pop-Location }
Push-Location "$Root\workers\freecad"
try {
    $FreeCadTemp = Join-Path "$Root\tmp" ("fc-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    python -m pytest -q --basetemp=$FreeCadTemp
    if ($LASTEXITCODE) { throw "FreeCAD tests failed" }
} finally { Pop-Location }
docker compose -f "$Root\compose.yaml" config --quiet
if ($LASTEXITCODE) { throw "Compose validation failed" }

docker compose -f "$Root\compose.yaml" build step-worker
if ($LASTEXITCODE) { throw "Failed to build step-worker service" }
docker compose -f "$Root\compose.yaml" run --rm --no-deps step-worker python -m pytest -q /app/tests
if ($LASTEXITCODE) { throw "STEP worker tests failed" }
docker compose -f "$Root\compose.yaml" up -d step-worker
if ($LASTEXITCODE) { throw "Failed to start step-worker service" }
try {
    $ready = Invoke-RestMethod http://localhost:8080/ready
    if ($ready.status -ne "ready") { throw "step-worker readiness failed" }
    $capabilities = Invoke-RestMethod -Headers @{ "X-Modelryx-Worker-Key" = $env:MODELRYX_INTERNAL_WORKER_KEY } `
        http://localhost:8080/capabilities
    if ($capabilities.worker_type -ne "STEP") { throw "step-worker capabilities failed" }
} finally {
    docker compose -f "$Root\compose.yaml" stop step-worker | Out-Null
}
