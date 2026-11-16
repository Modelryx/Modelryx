param(
    [string]$InstallDirectory = "$env:LOCALAPPDATA\Modelryx\LocalCadAgent",
    [string]$Runtime = "win-x64",
    [string[]]$AllowedOrigins = @(),
    [string]$FreeCadCommand = "",
    [Alias("BuildAdaptersFromSource")]
    [switch]$BuildPreprocessorsFromSource,
    [string]$AutoCadTargetFramework = "net10.0-windows",
    [string]$AutoCadSdkDir = "",
    [string]$NxDirectory = 'C:\Program Files\Siemens\NX2506',
    [switch]$BuildNxPreprocessorFromSource
)

$ErrorActionPreference = "Stop"
$Project = Join-Path $PSScriptRoot "Modelryx.LocalCad.Agent\Modelryx.LocalCad.Agent.csproj"
$Publish = Join-Path $InstallDirectory "app"
New-Item -ItemType Directory -Force -Path $Publish | Out-Null
$Prebuilt = Join-Path $PSScriptRoot "artifacts\$Runtime\Modelryx.LocalCad.Agent.exe"
$sourceInstall = $BuildPreprocessorsFromSource -or $BuildNxPreprocessorFromSource -or
    -not (Test-Path -LiteralPath $Prebuilt)
if ($BuildPreprocessorsFromSource -or $BuildNxPreprocessorFromSource) {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
        throw "Building the Local CAD Agent from source requires the .NET SDK."
    }
    dotnet publish $Project -c Release -r $Runtime --self-contained true `
        -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o $Publish
    if ($LASTEXITCODE) { throw "Local CAD Agent publish failed." }
} elseif (Test-Path -LiteralPath $Prebuilt) {
    Copy-Item -Force -LiteralPath $Prebuilt -Destination $Publish
    $PrebuiltPreprocessors = Join-Path $PSScriptRoot "artifacts\$Runtime\preprocessors"
    if (Test-Path -LiteralPath $PrebuiltPreprocessors) {
        Copy-Item -Recurse -Force -LiteralPath $PrebuiltPreprocessors -Destination $Publish
    }
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
# Remove the legacy local adapter layout. Server extraction binaries do not belong in this install.
$legacyAdapters = Join-Path $Publish "adapters"
if (Test-Path -LiteralPath $legacyAdapters) { Remove-Item -Recurse -Force -LiteralPath $legacyAdapters }

function Copy-PreprocessorFile([string]$Source, [string]$Cad, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Source)) { return $null }
    $destination = Join-Path (Join-Path $Preprocessors $Cad) $Name
    New-Item -ItemType Directory -Force -Path (Split-Path $destination) | Out-Null
    Copy-Item -Force -LiteralPath $Source -Destination $destination
    return $destination
}

$freeCadScript = Copy-PreprocessorFile `
    (Join-Path $PSScriptRoot "..\workers\freecad\preprocessor\modelryx_freecad_preprocessor.py") "freecad" "modelryx_freecad_preprocessor.py"
$freeCadShared = Copy-PreprocessorFile `
    (Join-Path $PSScriptRoot "..\workers\freecad\shared\modelryx_freecad_shared.py") "freecad" "modelryx_freecad_shared.py"
$freeCadConverterSource = Join-Path $PSScriptRoot "..\workers\freecad\converter"
if (Test-Path -LiteralPath $freeCadConverterSource) {
    foreach ($source in Get-ChildItem -LiteralPath $freeCadConverterSource -Filter '*.py' -File |
        Where-Object { $_.Name -notlike '*_test.py' }) {
        Copy-PreprocessorFile $source.FullName "freecad" $source.Name | Out-Null
    }
}
$freeCadConverter = Join-Path $Preprocessors "freecad\fcstd_to_modelryx_glb.py"
if (-not (Test-Path -LiteralPath $freeCadConverter)) { $freeCadConverter = $null }
if (-not $freeCadScript) {
    $installedScript = Join-Path $Preprocessors "freecad\modelryx_freecad_preprocessor.py"
    if (Test-Path -LiteralPath $installedScript) { $freeCadScript = $installedScript }
}
$legacyFreeCadMetadata = Join-Path $Preprocessors "freecad\modelryx_freecad_metadata.py"
if (Test-Path -LiteralPath $legacyFreeCadMetadata) {
    Remove-Item -Force -LiteralPath $legacyFreeCadMetadata
}
$autoCadDirectory = Join-Path $Publish "preprocessors\autocad"
$autoCadProject = Join-Path $PSScriptRoot "..\autocad_worker\src\Modelryx.AutoCAD.Preprocessor\Modelryx.AutoCAD.Preprocessor.csproj"
$autoCadOutput = Join-Path $PSScriptRoot "..\autocad_worker\src\Modelryx.AutoCAD.Preprocessor\bin\Release\$AutoCadTargetFramework"
$autoCadPreprocessor = Join-Path $autoCadDirectory "Modelryx.AutoCAD.Preprocessor.dll"
if ($BuildPreprocessorsFromSource -and -not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "Building CAD preprocessors from source requires the matching .NET SDK. Install a release package for SDK-free installation."
}
if ($BuildPreprocessorsFromSource -and (Test-Path -LiteralPath $autoCadProject)) {
    $buildArguments = @(
        "build", $autoCadProject, "-c", "Release",
        "/p:AutoCadTargetFramework=$AutoCadTargetFramework"
    )
    if (-not [string]::IsNullOrWhiteSpace($AutoCadSdkDir)) {
        $buildArguments += "/p:AutoCadSdkDir=$AutoCadSdkDir"
    }
    & dotnet @buildArguments
    if ($LASTEXITCODE) { throw "AutoCAD preprocessor build failed." }
}
New-Item -ItemType Directory -Force -Path $autoCadDirectory | Out-Null
if ($BuildPreprocessorsFromSource) {
    foreach ($source in Get-ChildItem -LiteralPath $autoCadOutput -File | Where-Object {
        $_.Extension -in @(".dll", ".json")
    }) {
        Copy-Item -Force -LiteralPath $source.FullName -Destination $autoCadDirectory
    }
}
if (-not (Test-Path -LiteralPath $autoCadPreprocessor)) { $autoCadPreprocessor = $null }
$inventorDirectory = Join-Path $Publish "preprocessors\inventor"
$inventorPreprocessor = Join-Path $inventorDirectory "Modelryx.Inventor.Preprocessor.exe"
$inventorProject = Join-Path $PSScriptRoot "..\inventor_worker\src\Modelryx.Inventor.Preprocessor\Modelryx.Inventor.Preprocessor.csproj"
if ($BuildPreprocessorsFromSource -and (Test-Path -LiteralPath $inventorProject)) {
    New-Item -ItemType Directory -Force -Path $inventorDirectory | Out-Null
    dotnet publish $inventorProject -c Release -r $Runtime --self-contained true `
        -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o $inventorDirectory
    if ($LASTEXITCODE) { throw "Inventor preprocessor publish failed." }
}
if (-not (Test-Path -LiteralPath $inventorPreprocessor)) { $inventorPreprocessor = $null }

$nxPreprocessorDirectory = Join-Path $Publish 'preprocessors\nx'
$nxPreprocessor = Join-Path $nxPreprocessorDirectory 'Modelryx.NX.Preprocessor.exe'
$nxProject = Join-Path $PSScriptRoot '..\nx_worker\src\Modelryx.NX.Preprocessor\Modelryx.NX.Preprocessor.csproj'
$nxManaged = Join-Path $NxDirectory 'NXBIN\managed_core'
$nxInstalled = (Test-Path -LiteralPath (Join-Path $nxManaged 'NXOpen.dll')) -and
    (Test-Path -LiteralPath (Join-Path $nxManaged 'NXOpen.UF.dll')) -and
    (Test-Path -LiteralPath (Join-Path $NxDirectory 'NXBIN\libufun.dll'))
if ($BuildNxPreprocessorFromSource -or (($BuildPreprocessorsFromSource -or $sourceInstall) -and $nxInstalled)) {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { throw 'Building the NX preprocessor requires the .NET 8 SDK.' }
    if (-not $nxInstalled) { throw "A complete NX installation was not found: $NxDirectory" }
    Write-Host "Building the local NX preprocessor for $NxDirectory"
    New-Item -ItemType Directory -Force -Path $nxPreprocessorDirectory | Out-Null
    dotnet publish $nxProject -c Release -r $Runtime --self-contained true `
        -p:PublishSingleFile=false "-p:NxOpenDirectory=$nxManaged" -o $nxPreprocessorDirectory
    if ($LASTEXITCODE) { throw 'NX preprocessor publish failed.' }
}
if (-not (Test-Path -LiteralPath $nxPreprocessor)) { $nxPreprocessor = $null }

if (-not $autoCadPreprocessor) {
    Write-Warning "The release package does not contain an AutoCAD preprocessor. AutoCAD files will be unavailable. Developers can rerun with -BuildPreprocessorsFromSource and the SDK required by their AutoCAD release."
}
if (-not $inventorPreprocessor) {
    Write-Warning "The release package does not contain an Inventor preprocessor. Inventor files will be unavailable. Developers can rerun with -BuildPreprocessorsFromSource."
}
if (-not $nxPreprocessor) {
    Write-Warning 'The release package does not contain an NX preprocessor. NX files will be unavailable. Developers can build it with -BuildNxPreprocessorFromSource -NxDirectory <installed NX directory>.'
}

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
    InventorPreprocessorExecutable = $inventorPreprocessor
    FreeCadCommand = $FreeCadCommand
    FreeCadPreprocessorScript = $freeCadScript
    FreeCadConverterScript = $freeCadConverter
    AutoCadPreprocessorDll = $autoCadPreprocessor
    NxDirectory = $NxDirectory
    NxPreprocessorExecutable = $nxPreprocessor
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
