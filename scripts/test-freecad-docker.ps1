param(
    [string]$Image = 'modelryx-freecad-tests'
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot

function Invoke-DockerCheck {
    param(
        [string]$Label,
        [string[]]$DockerArguments
    )

    Write-Host "`n$Label" -ForegroundColor Cyan
    & docker @DockerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed (Docker exit code $LASTEXITCODE)."
    }
}

Get-Command docker -ErrorAction Stop | Out-Null
Push-Location $repositoryRoot
try {
    Invoke-DockerCheck '1/6 Build FreeCAD test image' @(
        'build', '--target', 'native-tests', '-t', $Image, 'workers/freecad'
    )
    Invoke-DockerCheck '2/6 Unit tests' @(
        'run', '--rm', $Image, 'python', '-m', 'pytest', '-q', 'tests'
    )
    Invoke-DockerCheck '3/6 Golden native fixtures' @(
        'run', '--rm', $Image, 'python', 'converter/golden_integration_test.py'
    )
    Invoke-DockerCheck '4/6 Nested dependency fixtures' @(
        'run', '--rm', $Image, 'python', 'preprocessor/dependency_integration_test.py'
    )
    Invoke-DockerCheck '5/6 Production native fixtures' @(
        'run', '--rm', $Image, 'python', 'converter/production_fixture_test.py'
    )
    Invoke-DockerCheck '6/6 Process-tree cleanup fixtures' @(
        'run', '--rm', $Image, 'python', 'tests/process_tree_integration_test.py'
    )

    Write-Host "`nAll six FreeCAD Docker checks passed." -ForegroundColor Green
}
finally {
    Pop-Location
}
