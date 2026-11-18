param(
    [string]$InstallDirectory = "$env:LOCALAPPDATA\Modelryx\LocalCadAgent",
    [string]$Runtime = "win-x64",
    [string[]]$AllowedOrigins = @(),
    [switch]$BuildFromSource
)

$ErrorActionPreference = "Stop"
$Project = Join-Path $PSScriptRoot "Modelryx.LocalCad.Agent\Modelryx.LocalCad.Agent.csproj"
$Publish = Join-Path $InstallDirectory "app"
New-Item -ItemType Directory -Force -Path $Publish | Out-Null
$Prebuilt = Join-Path $PSScriptRoot "artifacts\$Runtime\Modelryx.LocalCad.Agent.exe"
if ($BuildFromSource) {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
        throw "Building the Local CAD Agent from source requires the .NET SDK."
    }
    dotnet publish $Project -c Release -r $Runtime --self-contained true `
        -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o $Publish
    if ($LASTEXITCODE) { throw "Local CAD Agent publish failed." }
} elseif (Test-Path -LiteralPath $Prebuilt) {
    Copy-Item -Force -LiteralPath $Prebuilt -Destination $Publish
} else {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
        throw "The prebuilt $Runtime application is missing. Obtain the release package; .NET is required only to build from source."
    }
    dotnet publish $Project -c Release -r $Runtime --self-contained true `
        -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o $Publish
    if ($LASTEXITCODE) { throw "Local CAD Agent publish failed." }
}

$Executable = Join-Path $Publish "Modelryx.LocalCad.Agent.exe"
Copy-Item -Force -LiteralPath (Join-Path $PSScriptRoot 'register-preprocessor.ps1') -Destination $Publish
$Preprocessors = Join-Path $Publish "preprocessors"
New-Item -ItemType Directory -Force -Path $Preprocessors | Out-Null
$settingsPath = Join-Path $Publish "settings.json"
$previousSettings = $null
if (Test-Path -LiteralPath $settingsPath) {
    try { $previousSettings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json }
    catch { throw "Existing Local CAD Agent settings could not be read; refusing to overwrite them." }
}
$effectiveAllowedOrigins = @($AllowedOrigins | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
if (-not $PSBoundParameters.ContainsKey('AllowedOrigins') -and $previousSettings) {
    try {
        $effectiveAllowedOrigins = @($previousSettings.AllowedOrigins | Where-Object {
            -not [string]::IsNullOrWhiteSpace($_)
        })
    } catch {
        Write-Warning 'Existing Local CAD Agent settings could not be read; configured web origins will not be preserved.'
    }
}
$settings = [ordered]@{
    ExternalPreprocessors = @($previousSettings.ExternalPreprocessors | Where-Object { $null -ne $_ })
    AllowedOrigins = $effectiveAllowedOrigins
}
$settings | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $settingsPath
$Protocol = "HKCU:\Software\Classes\modelryx-cad"
New-Item -Force -Path $Protocol | Out-Null
Set-Item -Path $Protocol -Value "URL:Modelryx CAD Protocol"
New-ItemProperty -Force -Path $Protocol -Name "URL Protocol" -Value "" | Out-Null
New-Item -Force -Path (Join-Path $Protocol "shell\open\command") | Out-Null
Set-Item -Path (Join-Path $Protocol "shell\open\command") -Value ('"{0}" "%1"' -f $Executable)

Write-Host "Modelryx Local CAD Agent installed: $Executable"
Write-Host "The application is self-contained; the user does not need to install .NET."
