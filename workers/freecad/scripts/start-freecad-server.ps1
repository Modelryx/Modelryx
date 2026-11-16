[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path $PSScriptRoot '..\..\..\.env'),
    [ValidateRange(1, 65535)][int]$Port = 8090,
    [string]$ListenAddress = '127.0.0.1',
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Environment file not found: $EnvFile. Create it from .env.example and set MODELRYX_INTERNAL_WORKER_KEY, or pass -EnvFile."
}
$environmentFile = (Resolve-Path -LiteralPath $EnvFile).Path
$composeFile = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\compose.standalone.yaml')).Path
$savedPort = $env:MODELRYX_FREECAD_PORT
$savedAddress = $env:MODELRYX_FREECAD_BIND_ADDRESS
try {
    $env:MODELRYX_FREECAD_PORT = [string]$Port
    $env:MODELRYX_FREECAD_BIND_ADDRESS = $ListenAddress
    $composeArguments = @('compose', '--project-name', 'modelryx-freecad-standalone',
        '--file', $composeFile, '--env-file', $environmentFile)
    & docker @composeArguments config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'The standalone FreeCAD configuration is invalid.' }
    $startArguments = @('up', '-d', '--wait', '--wait-timeout', '120')
    if (-not $SkipBuild) { $startArguments += '--build' }
    & docker @composeArguments @startArguments
    if ($LASTEXITCODE -ne 0) {
        throw 'The standalone FreeCAD worker did not become ready. Inspect logs with docker compose --project-name modelryx-freecad-standalone --file workers/freecad/compose.standalone.yaml logs freecad.'
    }
    Write-Host "Standalone FreeCAD is ready on port $Port (bind address $ListenAddress)."
    Write-Host 'Register its server-reachable URL in Administrator > External CAD Workers using the same internal worker key.'
    Write-Host 'When Modelryx runs in Docker Desktop, use -ListenAddress 0.0.0.0 and register http://host.docker.internal:PORT.'
}
finally {
    $env:MODELRYX_FREECAD_PORT = $savedPort
    $env:MODELRYX_FREECAD_BIND_ADDRESS = $savedAddress
}
