[CmdletBinding()]
param(
    [string]$AutoCadInstallDir = "",
    [string]$InventorInstallDir = "",
    [string]$FreeCadCmd = "",
    [string]$AdminUsername = "",
    [string]$AdminPassword = "",
    [string]$WorkerKey = "",
    [string]$JwtSecret = "",
    [string]$AutoCadWorkerUrl = "http://host.docker.internal:5080",
    [string]$InventorWorkerUrl = "http://host.docker.internal:5091",
    [string]$WindowsServerUrl = "http://127.0.0.1:8000",
    [string]$WindowsStepWorkerUrl = "http://127.0.0.1:8080",
    [switch]$NonInteractive,
    [switch]$SkipAutoCad,
    [switch]$SkipInventor,
    [switch]$BuildCadAdapters
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Get-EnvMap([string]$Path)
{
    $Values = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $Values }
    foreach ($Line in Get-Content -LiteralPath $Path)
    {
        $Text = $Line.Trim()
        if (-not $Text -or $Text.StartsWith("#") -or -not $Text.Contains("=")) { continue }
        $Parts = $Text -split "=", 2
        $Values[$Parts[0].Trim()] = $Parts[1].Trim().Trim('"').Trim("'")
    }
    return $Values
}

function Write-EnvFile([string]$TemplatePath, [string]$DestinationPath, [hashtable]$Overrides)
{
    $Existing = Get-EnvMap $DestinationPath
    $Written = @{}
    $Lines = New-Object System.Collections.Generic.List[string]
    foreach ($Line in Get-Content -LiteralPath $TemplatePath)
    {
        $Text = $Line.Trim()
        if (-not $Text -or $Text.StartsWith("#") -or -not $Text.Contains("="))
        {
            $Lines.Add($Line)
            continue
        }
        $Name = ($Text -split "=", 2)[0].Trim()
        $Value = if ($Overrides.ContainsKey($Name)) { $Overrides[$Name] }
            elseif ($Existing.ContainsKey($Name)) { $Existing[$Name] }
            else { ($Text -split "=", 2)[1] }
        if ($null -eq $Value) { $Value = "" }
        if ([string]$Value -match "[`r`n]") { throw "$Name cannot contain a newline." }
        $Lines.Add("$Name=$Value")
        $Written[$Name] = $true
    }
    foreach ($Name in $Overrides.Keys | Sort-Object)
    {
        if (-not $Written.ContainsKey($Name)) { $Lines.Add("$Name=$($Overrides[$Name])") }
    }
    foreach ($Name in $Existing.Keys | Sort-Object)
    {
        if (-not $Written.ContainsKey($Name) -and -not $Overrides.ContainsKey($Name)) {
            $Lines.Add("$Name=$($Existing[$Name])")
        }
    }
    $Directory = Split-Path -Parent $DestinationPath
    if (-not (Test-Path -LiteralPath $Directory)) { New-Item -ItemType Directory -Path $Directory | Out-Null }
    [IO.File]::WriteAllLines($DestinationPath, $Lines, $Utf8NoBom)
    Write-Host "Configured $DestinationPath"
}

function New-RandomSecret([int]$Bytes = 48)
{
    $Buffer = New-Object byte[] $Bytes
    $Generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $Generator.GetBytes($Buffer) } finally { $Generator.Dispose() }
    return [Convert]::ToBase64String($Buffer)
}

function Read-Value([string]$Label, [string]$Default)
{
    if ($NonInteractive) { return $Default }
    $Display = if ([string]::IsNullOrWhiteSpace($Default)) { "" } else { " [$Default]" }
    $Value = Read-Host "$Label$Display"
    if ([string]::IsNullOrWhiteSpace($Value)) { return $Default }
    return $Value.Trim().Trim('"')
}

function Find-AutodeskInstall([string]$Product, [string]$ExecutableRelativePath)
{
    $Roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, "F:\Autodesk") |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path -LiteralPath $_) }
    foreach ($Root in $Roots)
    {
        $Base = if ((Split-Path -Leaf $Root) -ieq "Autodesk") { $Root } else { Join-Path $Root "Autodesk" }
        if (-not (Test-Path -LiteralPath $Base)) { continue }
        $Match = Get-ChildItem -LiteralPath $Base -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "$Product *" -and (Test-Path -LiteralPath (Join-Path $_.FullName $ExecutableRelativePath)) } |
            Sort-Object Name -Descending | Select-Object -First 1
        if ($Match) { return $Match.FullName }
    }
    return ""
}

function Find-FreeCadCommand
{
    $Candidates = @(
        "C:\Program Files\FreeCAD 1.1\bin\python.exe",
        "C:\Program Files\FreeCAD 1.0\bin\python.exe",
        "C:\Program Files\FreeCAD 0.21\bin\python.exe",
        "C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe",
        "C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe",
        "C:\Program Files\FreeCAD 0.21\bin\FreeCADCmd.exe"
    )
    return $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

function Assert-HttpUrl([string]$Name, [string]$Value)
{
    $Parsed = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$Parsed) -or
        $Parsed.Scheme -notin @("http", "https"))
    {
        throw "$Name must be an absolute HTTP or HTTPS URL."
    }
}

$RootEnv = Get-EnvMap (Join-Path $ProjectRoot ".env")
if ([string]::IsNullOrWhiteSpace($WorkerKey))
{
    $WorkerKey = if ($RootEnv.ContainsKey("MODELRYX_INTERNAL_WORKER_KEY") -and
        $RootEnv["MODELRYX_INTERNAL_WORKER_KEY"] -notlike "replace-*") {
        $RootEnv["MODELRYX_INTERNAL_WORKER_KEY"]
    } else { New-RandomSecret }
}
if ([string]::IsNullOrWhiteSpace($JwtSecret))
{
    $JwtSecret = if ($RootEnv.ContainsKey("MODELRYX_JWT_SECRET") -and
        $RootEnv["MODELRYX_JWT_SECRET"] -notlike "replace-*" -and
        $RootEnv["MODELRYX_JWT_SECRET"] -ne "change-this-development-secret") {
        $RootEnv["MODELRYX_JWT_SECRET"]
    } else { New-RandomSecret }
}
$ExistingAdmin = if ($RootEnv.ContainsKey("MODELRYX_BOOTSTRAP_ADMIN_USERNAME")) {
    $RootEnv["MODELRYX_BOOTSTRAP_ADMIN_USERNAME"]
} else { "admin" }
$AdminUsername = Read-Value "Bootstrap administrator username" $(if ($AdminUsername) { $AdminUsername } else { $ExistingAdmin })
if ([string]::IsNullOrWhiteSpace($AdminPassword))
{
    if ($RootEnv.ContainsKey("MODELRYX_BOOTSTRAP_ADMIN_PASSWORD") -and
        -not [string]::IsNullOrWhiteSpace($RootEnv["MODELRYX_BOOTSTRAP_ADMIN_PASSWORD"]))
    {
        $AdminPassword = $RootEnv["MODELRYX_BOOTSTRAP_ADMIN_PASSWORD"]
    }
    elseif ($NonInteractive)
    {
        $AdminPassword = "admin"
    }
    else
    {
        $SecurePassword = Read-Host "Bootstrap administrator password" -AsSecureString
        $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePassword)
        try { $AdminPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer) }
        if ([string]::IsNullOrWhiteSpace($AdminPassword)) { throw "Administrator password cannot be empty." }
    }
}

if (-not $SkipAutoCad)
{
    if ([string]::IsNullOrWhiteSpace($AutoCadInstallDir)) {
        $AutoCadInstallDir = Find-AutodeskInstall "AutoCAD" "accoreconsole.exe"
    }
    $AutoCadInstallDir = Read-Value "AutoCAD installation directory" $AutoCadInstallDir
    if ([string]::IsNullOrWhiteSpace($AutoCadInstallDir)) { throw "AutoCAD was not found. Pass -SkipAutoCad or -AutoCadInstallDir." }
    $AutoCadInstallDir = [IO.Path]::GetFullPath($AutoCadInstallDir)
    foreach ($Required in @("accoreconsole.exe", "AcDbMgd.dll"))
    {
        if (-not (Test-Path -LiteralPath (Join-Path $AutoCadInstallDir $Required))) {
            throw "AutoCAD file was not found: $(Join-Path $AutoCadInstallDir $Required)"
        }
    }
}

if (-not $SkipInventor)
{
    if ([string]::IsNullOrWhiteSpace($InventorInstallDir)) {
        $InventorInstallDir = Find-AutodeskInstall "Inventor" "Bin\Inventor.exe"
    }
    $InventorInstallDir = Read-Value "Inventor installation directory" $InventorInstallDir
    if ([string]::IsNullOrWhiteSpace($InventorInstallDir)) { throw "Inventor was not found. Pass -SkipInventor or -InventorInstallDir." }
    $InventorInstallDir = [IO.Path]::GetFullPath($InventorInstallDir)
    foreach ($Required in @("Bin\Inventor.exe", "Bin\Public Assemblies\Autodesk.Inventor.Interop.dll"))
    {
        if (-not (Test-Path -LiteralPath (Join-Path $InventorInstallDir $Required))) {
            throw "Inventor file was not found: $(Join-Path $InventorInstallDir $Required)"
        }
    }
}

if ([string]::IsNullOrWhiteSpace($FreeCadCmd))
{
    $FreeCadCmd = if ($RootEnv.ContainsKey("MODELRYX_FREECAD_CMD")) {
        $RootEnv["MODELRYX_FREECAD_CMD"]
    } else { Find-FreeCadCommand }
}
$FreeCadCmd = Read-Value "FreeCAD Python or FreeCADCmd executable" $FreeCadCmd
if (-not [string]::IsNullOrWhiteSpace($FreeCadCmd))
{
    $FreeCadCmd = [IO.Path]::GetFullPath($FreeCadCmd)
    if (-not (Test-Path -LiteralPath $FreeCadCmd -PathType Leaf)) {
        throw "FreeCAD command was not found: $FreeCadCmd"
    }
}

$AutoCadWorkerUrl = Read-Value "AutoCAD URL visible from Docker" $AutoCadWorkerUrl
$InventorWorkerUrl = Read-Value "Inventor URL visible from Docker" $InventorWorkerUrl
Assert-HttpUrl "AutoCAD worker URL" $AutoCadWorkerUrl
Assert-HttpUrl "Inventor worker URL" $InventorWorkerUrl
Assert-HttpUrl "Windows Modelryx server URL" $WindowsServerUrl
Assert-HttpUrl "Windows STEP worker URL" $WindowsStepWorkerUrl

$RootOverrides = @{
    MODELRYX_JWT_SECRET = $JwtSecret
    MODELRYX_BOOTSTRAP_ADMIN_USERNAME = $AdminUsername
    MODELRYX_BOOTSTRAP_ADMIN_PASSWORD = $AdminPassword
    MODELRYX_INTERNAL_WORKER_KEY = $WorkerKey
    MODELRYX_AUTOCAD_WORKER_URL = $AutoCadWorkerUrl.TrimEnd('/')
    MODELRYX_INVENTOR_WORKER_URL = $InventorWorkerUrl.TrimEnd('/')
    MODELRYX_FREECAD_CMD = $FreeCadCmd
}
Write-EnvFile (Join-Path $ProjectRoot ".env.example") (Join-Path $ProjectRoot ".env") $RootOverrides

$ServerOverrides = @{}
foreach ($Name in $RootOverrides.Keys) { $ServerOverrides[$Name] = $RootOverrides[$Name] }
Write-EnvFile (Join-Path $ProjectRoot "server\.env.example") (Join-Path $ProjectRoot "server\.env") $ServerOverrides

if (-not $SkipAutoCad)
{
    $AutoCadPlugin = Join-Path $ProjectRoot "autocad_worker\artifacts\autocad-2027\Modelryx.bundle\Contents\Windows\Modelryx.AutoCAD.Plugin.dll"
    $AutoCadOverrides = @{
        Worker__ApiKey = $WorkerKey
        ContractWorker__CoreConsole = (Join-Path $AutoCadInstallDir "accoreconsole.exe")
        ContractWorker__PluginDll = $AutoCadPlugin
        StepWorker__ServiceUrl = $WindowsStepWorkerUrl
        StepWorker__ApiKey = $WorkerKey
        MODELRYX_SERVER_URL = $WindowsServerUrl
        MODELRYX_INTERNAL_WORKER_KEY = $WorkerKey
        MODELRYX_AUTOCAD_CORE_CONSOLE = (Join-Path $AutoCadInstallDir "accoreconsole.exe")
        MODELRYX_AUTOCAD_PRODUCT = "ACAD"
        MODELRYX_STEP_TO_GLB_URL = $WindowsStepWorkerUrl
    }
    Write-EnvFile (Join-Path $ProjectRoot "autocad_worker\.env.example") (Join-Path $ProjectRoot "autocad_worker\.env") $AutoCadOverrides
}

if (-not $SkipInventor)
{
    $InventorAdapter = Join-Path $ProjectRoot "inventor_worker\src\Modelryx.Inventor.Adapter\bin\Release\net8.0-windows\Modelryx.Inventor.Adapter.exe"
    $InventorOverrides = @{
        Worker__ApiKey = $WorkerKey
        Worker__AdapterExecutable = $InventorAdapter
        StepWorker__ServiceUrl = $WindowsStepWorkerUrl
        StepWorker__ApiKey = $WorkerKey
        MODELRYX_INVENTOR_EXE = (Join-Path $InventorInstallDir "Bin\Inventor.exe")
        MODELRYX_SERVER_URL = $WindowsServerUrl
        MODELRYX_INTERNAL_WORKER_KEY = $WorkerKey
        MODELRYX_STEP_WORKER_URL = $WindowsStepWorkerUrl
    }
    Write-EnvFile (Join-Path $ProjectRoot "inventor_worker\.env.example") (Join-Path $ProjectRoot "inventor_worker\.env") $InventorOverrides
}

function Escape-Xml([string]$Value) { return [Security.SecurityElement]::Escape($Value) }
$Properties = New-Object System.Collections.Generic.List[string]
$Properties.Add('<Project>')
$Properties.Add('  <PropertyGroup>')
if (-not $SkipAutoCad) { $Properties.Add("    <AutoCadSdkDir>$(Escape-Xml $AutoCadInstallDir)</AutoCadSdkDir>") }
if (-not $SkipInventor) { $Properties.Add("    <InventorInstallDir>$(Escape-Xml $InventorInstallDir)</InventorInstallDir>") }
if (-not [string]::IsNullOrWhiteSpace($FreeCadCmd)) { $Properties.Add("    <FreeCadCmd>$(Escape-Xml $FreeCadCmd)</FreeCadCmd>") }
$Properties.Add('  </PropertyGroup>')
$Properties.Add('</Project>')
[IO.File]::WriteAllLines((Join-Path $ProjectRoot ".modelryx.local.props"), $Properties, $Utf8NoBom)
Write-Host "Configured $(Join-Path $ProjectRoot '.modelryx.local.props')"

if ($BuildCadAdapters)
{
    if (-not $SkipAutoCad) { & (Join-Path $ProjectRoot "autocad_worker\scripts\build-autocad-bundle.ps1") }
    if (-not $SkipInventor) {
        dotnet build (Join-Path $ProjectRoot "inventor_worker\src\Modelryx.Inventor.Adapter\Modelryx.Inventor.Adapter.csproj") --configuration Release
        if ($LASTEXITCODE) { throw "Inventor adapter build failed with exit code $LASTEXITCODE." }
    }
}

Write-Host ""
Write-Host "Modelryx configuration is ready."
Write-Host "1. Start Docker services: docker compose up --build"
if (-not $SkipAutoCad) { Write-Host "2. Start AutoCAD worker: .\autocad_worker\scripts\start-autocad-server.ps1" }
if (-not $SkipInventor) { Write-Host "3. Start Inventor worker: .\inventor_worker\scripts\start-inventor-server.ps1" }
