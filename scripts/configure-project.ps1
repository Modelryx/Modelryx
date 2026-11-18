[CmdletBinding()]
param(
    [string]$FreeCadCmd = "",
    [string]$AdminUsername = "",
    [string]$AdminPassword = "",
    [string]$WorkerKey = "",
    [string]$JwtSecret = "",
    [string]$PostgresPassword = "",
    [int]$PostgresPort = 5432,
    [string]$Environment = "",
    [Alias("CadAnalyzerUrl")][string]$FreeCadPreprocessorUrl = "",
    [string]$FreeCadWorkerUrl = "",
    [string]$AiGatewayBaseUrl = "",
    [string]$AiGatewayApiKey = "",
    [string]$AiChatModel = "",
    [string]$AiEmbeddingModel = "",
    [string]$WindowsServerUrl = "http://127.0.0.1:8000",
    [string]$WindowsStepWorkerUrl = "http://127.0.0.1:8080",
    [switch]$NonInteractive,
    [switch]$InstallLocalCadAgent
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
    return [Convert]::ToBase64String($Buffer).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Read-Value([string]$Label, [string]$Default)
{
    if ($NonInteractive) { return $Default }
    $Display = if ([string]::IsNullOrWhiteSpace($Default)) { "" } else { " [$Default]" }
    $Value = Read-Host "$Label$Display"
    if ([string]::IsNullOrWhiteSpace($Value)) { return $Default }
    return $Value.Trim().Trim('"')
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

function Test-ProductionSecret([string]$Value, [int]$Minimum)
{
    $Normalized = $Value.Trim().ToLowerInvariant()
    if ($Normalized.Length -lt $Minimum -or $Normalized -in @("admin", "password", "changeme") -or
        $Normalized -match '^(replace-|change-this-|ci-only-|local-validation-)') { return $false }
    return (@($Normalized.ToCharArray() | Select-Object -Unique).Count -ge 8)
}

$RootEnv = Get-EnvMap (Join-Path $ProjectRoot ".env")
if (-not $Environment) {
    $Environment = if ($RootEnv.ContainsKey("MODELRYX_ENVIRONMENT")) { $RootEnv["MODELRYX_ENVIRONMENT"] } else { "development" }
}
$Environment = Read-Value "Environment (development, test, production)" $Environment
if ($Environment -notin @("development", "test", "production")) { throw "Environment must be development, test, or production." }
if ($PostgresPort -lt 1 -or $PostgresPort -gt 65535) { throw "PostgresPort must be between 1 and 65535." }
if (-not $PSBoundParameters.ContainsKey("PostgresPort") -and $RootEnv.ContainsKey("MODELRYX_POSTGRES_PORT")) {
    if (-not [int]::TryParse($RootEnv["MODELRYX_POSTGRES_PORT"], [ref]$PostgresPort) -or $PostgresPort -lt 1 -or $PostgresPort -gt 65535) { throw "Existing MODELRYX_POSTGRES_PORT is invalid." }
}
if (-not $PostgresPassword) {
    if ($RootEnv.ContainsKey("POSTGRES_PASSWORD") -and $RootEnv["POSTGRES_PASSWORD"]) {
        $PostgresPassword = $RootEnv["POSTGRES_PASSWORD"]
        if ($PostgresPassword -like "replace-*") { throw "Existing POSTGRES_PASSWORD is a placeholder. Set -PostgresPassword explicitly after checking the database credentials." }
    } else { $PostgresPassword = New-RandomSecret }
}
if ($PSBoundParameters.ContainsKey("PostgresPassword") -and $RootEnv.ContainsKey("POSTGRES_PASSWORD") -and
    $RootEnv["POSTGRES_PASSWORD"] -and $RootEnv["POSTGRES_PASSWORD"] -ne $PostgresPassword) {
    Write-Warning "Changing POSTGRES_PASSWORD here does not change credentials in an existing PostgreSQL volume. Update the database separately before using the new password."
}
# Compose interpolates the password into a database URL and dotenv file.
if ($PostgresPassword -notmatch '^[A-Za-z0-9_-]{16,}$') { throw "PostgresPassword must be at least 16 URL-safe characters (letters, digits, underscore, or hyphen)." }
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
        -not [string]::IsNullOrWhiteSpace($RootEnv["MODELRYX_BOOTSTRAP_ADMIN_PASSWORD"]) -and
        $RootEnv["MODELRYX_BOOTSTRAP_ADMIN_PASSWORD"] -notlike "replace-*")
    {
        $AdminPassword = $RootEnv["MODELRYX_BOOTSTRAP_ADMIN_PASSWORD"]
    }
    elseif ($NonInteractive)
    {
        $AdminPassword = New-RandomSecret
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

if ($Environment -eq "production") {
    if (-not (Test-ProductionSecret $AdminPassword 16)) { throw "Production administrator password must satisfy the server's password policy." }
    if (-not (Test-ProductionSecret $JwtSecret 32)) { throw "Production JWT secret must satisfy the server's secret policy." }
    if (-not (Test-ProductionSecret $WorkerKey 32)) { throw "Production worker key must satisfy the server's secret policy." }
}
foreach ($Secret in @($AdminPassword, $JwtSecret, $WorkerKey)) {
    if ($Secret -match '[\r\n$#"'']') { throw "Secrets cannot contain newlines, dollar signs, hash signs, or quotes in dotenv files." }
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

Assert-HttpUrl "Windows Modelryx server URL" $WindowsServerUrl
Assert-HttpUrl "Windows STEP worker URL" $WindowsStepWorkerUrl

$RootOverrides = @{
    POSTGRES_PASSWORD = $PostgresPassword
    MODELRYX_POSTGRES_PORT = "$PostgresPort"
    MODELRYX_ENVIRONMENT = $Environment
    MODELRYX_JWT_SECRET = $JwtSecret
    MODELRYX_BOOTSTRAP_ADMIN_USERNAME = $AdminUsername
    MODELRYX_BOOTSTRAP_ADMIN_PASSWORD = $AdminPassword
    MODELRYX_INTERNAL_WORKER_KEY = $WorkerKey
    MODELRYX_FREECAD_CMD = $FreeCadCmd
}
if (-not $PSBoundParameters.ContainsKey("FreeCadPreprocessorUrl") -and
    -not $RootEnv.ContainsKey("MODELRYX_FREECAD_PREPROCESSOR_URL") -and
    $RootEnv.ContainsKey("MODELRYX_CAD_ANALYZER_URL")) {
    $RootOverrides["MODELRYX_FREECAD_PREPROCESSOR_URL"] = $RootEnv["MODELRYX_CAD_ANALYZER_URL"]
}
$OptionalSettings = @{
    FreeCadPreprocessorUrl = "MODELRYX_FREECAD_PREPROCESSOR_URL"; FreeCadWorkerUrl = "MODELRYX_FREECAD_WORKER_URL"
    AiGatewayBaseUrl = "MODELRYX_AI_GATEWAY_BASE_URL"
    AiGatewayApiKey = "MODELRYX_AI_GATEWAY_API_KEY"; AiChatModel = "MODELRYX_AI_CHAT_MODEL"
    AiEmbeddingModel = "MODELRYX_AI_EMBEDDING_MODEL"
}
foreach ($Parameter in $OptionalSettings.Keys) {
    if ($PSBoundParameters.ContainsKey($Parameter)) {
        $Value = [string](Get-Variable -Name $Parameter -ValueOnly)
        if ($Parameter -match 'Url$' -and $Value) { Assert-HttpUrl $Parameter $Value }
        if ($Value -match '[\r\n$#"'']') { throw "$Parameter contains characters unsupported in dotenv values." }
        $RootOverrides[$OptionalSettings[$Parameter]] = $Value
    }
}
Write-EnvFile (Join-Path $ProjectRoot ".env.example") (Join-Path $ProjectRoot ".env") $RootOverrides

$ServerOverrides = @{}
foreach ($Name in $RootOverrides.Keys) { $ServerOverrides[$Name] = $RootOverrides[$Name] }
[void]$ServerOverrides.Remove("POSTGRES_PASSWORD")
[void]$ServerOverrides.Remove("MODELRYX_POSTGRES_PORT")
$ServerEnv = Get-EnvMap (Join-Path $ProjectRoot "server\.env")
$ExistingServerUrl = $ServerEnv["MODELRYX_DATABASE_URL"]
if ($ExistingServerUrl -and $ExistingServerUrl -notmatch '@db:5432/' -and
    -not $PSBoundParameters.ContainsKey("PostgresPassword") -and -not $PSBoundParameters.ContainsKey("PostgresPort")) {
    $ServerOverrides["MODELRYX_DATABASE_URL"] = $ExistingServerUrl
} else {
    $ServerOverrides["MODELRYX_DATABASE_URL"] = "postgresql+psycopg://modelryx:${PostgresPassword}@127.0.0.1:${PostgresPort}/modelryx"
}
$ServerOverrides["MODELRYX_STORAGE_ROOT"] = "data/objects"
$ServerOverrides["MODELRYX_SERVER_URL"] = $WindowsServerUrl
$ServerOverrides["MODELRYX_STEP_WORKER_URL"] = $WindowsStepWorkerUrl
Write-EnvFile (Join-Path $ProjectRoot "server\.env.example") (Join-Path $ProjectRoot "server\.env") $ServerOverrides

function Escape-Xml([string]$Value) { return [Security.SecurityElement]::Escape($Value) }
$Properties = New-Object System.Collections.Generic.List[string]
$Properties.Add('<Project>')
$Properties.Add('  <PropertyGroup>')
if (-not [string]::IsNullOrWhiteSpace($FreeCadCmd)) { $Properties.Add("    <FreeCadCmd>$(Escape-Xml $FreeCadCmd)</FreeCadCmd>") }
$Properties.Add('  </PropertyGroup>')
$Properties.Add('</Project>')
[IO.File]::WriteAllLines((Join-Path $ProjectRoot ".modelryx.local.props"), $Properties, $Utf8NoBom)
Write-Host "Configured $(Join-Path $ProjectRoot '.modelryx.local.props')"

if ($InstallLocalCadAgent) {
    $AgentInstaller = Join-Path $ProjectRoot "local_cad_agent\install-windows.ps1"
    & $AgentInstaller
}

Write-Host ""
Write-Host "Modelryx configuration is ready."
Write-Host "1. Start Docker services: docker compose up --build"
if (-not $InstallLocalCadAgent) { Write-Host "Local CAD selection requires the agent; use -InstallLocalCadAgent or see local_cad_agent/README.md." }
Write-Host "Optional AI and remote FreeCAD service settings can be passed as parameters or configured in .env."
if ($NonInteractive -and -not $PSBoundParameters.ContainsKey("AdminPassword")) { Write-Host "The generated bootstrap administrator password is stored in .env." }
