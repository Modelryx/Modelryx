[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z][a-z0-9_-]{0,63}$')][string]$WorkerId,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$')][string]$CadSystem,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')][string]$Version,
    [Parameter(Mandatory=$true)][string[]]$Extensions,
    [Parameter(Mandatory=$true)][string]$SourceDirectory,
    [Parameter(Mandatory=$true)][string]$Executable,
    [string]$InstallDirectory = "$env:LOCALAPPDATA\Modelryx\LocalCadAgent"
)
$ErrorActionPreference = 'Stop'
$app = Join-Path $InstallDirectory 'app'
$settingsPath = Join-Path $app 'settings.json'
if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
    throw 'Install the Modelryx Local CAD Agent before registering a preprocessor.'
}
$settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
$source = [IO.Path]::GetFullPath($SourceDirectory)
if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw 'SourceDirectory does not exist.' }
if ([IO.Path]::IsPathRooted($Executable) -or $Executable -match '(^|[\\/])\.\.([\\/]|$)') {
    throw 'Executable must be a relative path inside SourceDirectory.'
}
$sourceExecutable = [IO.Path]::GetFullPath((Join-Path $source $Executable))
if (-not $sourceExecutable.StartsWith($source.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-Path -LiteralPath $sourceExecutable -PathType Leaf)) {
    throw 'Executable does not exist inside SourceDirectory.'
}
foreach ($extension in $Extensions) {
    if ($extension -notmatch '^\.[A-Za-z0-9]{1,16}$') { throw "Invalid extension: $extension" }
}
$preprocessors = [IO.Path]::GetFullPath((Join-Path $app 'preprocessors'))
$destination = [IO.Path]::GetFullPath((Join-Path (Join-Path $preprocessors $WorkerId) $Version))
if (-not $destination.StartsWith($preprocessors.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The preprocessor destination is outside the installation directory.'
}
if ($source -eq $destination) { throw 'Source and destination must differ.' }
New-Item -ItemType Directory -Force -Path $destination | Out-Null
Get-ChildItem -LiteralPath $source -Force | Copy-Item -Destination $destination -Recurse -Force
$entry = [ordered]@{
    Id = $WorkerId; CadSystem = $CadSystem; Version = $Version
    Executable = (Join-Path $destination $Executable)
    Extensions = @($Extensions | ForEach-Object { $_.ToLowerInvariant() } | Select-Object -Unique)
}
$entries = @($settings.ExternalPreprocessors | Where-Object { $_ -and $_.Id -ne $WorkerId }) + @($entry)
if ($settings.PSObject.Properties['ExternalPreprocessors']) { $settings.ExternalPreprocessors = $entries }
else { $settings | Add-Member -NotePropertyName ExternalPreprocessors -NotePropertyValue $entries }
$temporary = "$settingsPath.tmp"
try {
    $settings | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $settingsPath -Force
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
}
Write-Host "Registered $WorkerId $Version in $settingsPath"
