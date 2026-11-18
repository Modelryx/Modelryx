[CmdletBinding()]
param([string]$Configuration = 'Release')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $root 'artifacts\preprocessor'
# Always produce a minimal distribution, including when rebuilding an earlier artifact.
$artifactRoot = [IO.Path]::GetFullPath((Join-Path $root 'artifacts'))
$destination = [IO.Path]::GetFullPath($destination)
if ($destination -ne (Join-Path $artifactRoot 'preprocessor')) {
    throw 'Refusing to clean an unexpected build output directory.'
}
if (Test-Path -LiteralPath $destination) {
    Remove-Item -LiteralPath $destination -Recurse -Force
}
dotnet publish (Join-Path $root 'preprocessor-host\Modelryx.FreeCAD.Preprocessor.csproj') -c $Configuration -o $destination
if ($LASTEXITCODE) { throw 'FreeCAD preprocessor host publish failed.' }
foreach ($directory in @('preprocessor', 'shared')) {
    $target = Join-Path $destination $directory
    New-Item -ItemType Directory -Force $target | Out-Null
    $sourceFile = if ($directory -eq 'preprocessor') { 'modelryx_freecad_preprocessor.py' } else { 'modelryx_freecad_shared.py' }
    Copy-Item -LiteralPath (Join-Path (Join-Path $root $directory) $sourceFile) -Destination $target -Force
}
Copy-Item (Join-Path $root 'preprocessor\local_preprocessor.py') $destination -Force
Write-Host "FreeCAD preprocessor distribution: $destination"
