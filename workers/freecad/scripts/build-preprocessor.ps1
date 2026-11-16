[CmdletBinding()]
param([string]$Configuration = 'Release')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $root 'artifacts\preprocessor'
dotnet publish (Join-Path $root 'preprocessor-host\Modelryx.FreeCAD.Preprocessor.csproj') -c $Configuration -o $destination
if ($LASTEXITCODE) { throw 'FreeCAD preprocessor host publish failed.' }
foreach ($directory in @('preprocessor', 'converter', 'shared')) {
    $target = Join-Path $destination $directory
    New-Item -ItemType Directory -Force $target | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $root $directory) -Filter '*.py' | Copy-Item -Destination $target -Force
}
Copy-Item (Join-Path $root 'preprocessor\local_preprocessor.py') $destination -Force
Write-Host "FreeCAD preprocessor distribution: $destination"
