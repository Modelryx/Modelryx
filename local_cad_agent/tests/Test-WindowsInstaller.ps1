# Exercise the installer as a black box; substitute publishing and protocol registration only.
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -and $env:OS -ne 'Windows_NT') { Write-Output 'SKIP: Windows installer checks require Windows.'; return }
$originalInstaller = Join-Path (Split-Path $PSScriptRoot -Parent) 'install-windows.ps1'
$converterSource = Join-Path $PSScriptRoot '../../workers/freecad/converter'
$converterFiles = @(Get-ChildItem -LiteralPath $converterSource -Filter '*.py' -File | Where-Object {
    $_.Name -notlike '*_test.py'
})
$tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$testRoot = Join-Path $tempParent ('modelryx-installer-' + [guid]::NewGuid().ToString('N'))
$state = [pscustomobject]@{ Calls = [Collections.Generic.List[object]]::new(); RegistryCalls = 0 }

function dotnet {
    $arguments = @($args)
    $outputIndex = [Array]::IndexOf($arguments, '-o')
    if ($arguments[0] -ne 'publish' -or $outputIndex -lt 0) { throw 'Unexpected dotnet invocation.' }
    $output = $arguments[$outputIndex + 1]
    [IO.Directory]::CreateDirectory($output) | Out-Null
    $project = [IO.Path]::GetFileNameWithoutExtension($arguments[1])
    [IO.File]::WriteAllText((Join-Path $output ($project + '.exe')), 'test publish marker')
    $state.Calls.Add([pscustomobject]@{ Project = $project; Arguments = $arguments })
    $global:LASTEXITCODE = 0
}
function New-Item {
    param([string]$Path, [string]$ItemType, [switch]$Force)
    if ($Path -like 'HKCU:\*') { $state.RegistryCalls++; return }
    Microsoft.PowerShell.Management\New-Item -Path $Path -ItemType $ItemType -Force:$Force
}
function Set-Item {
    param([string]$Path, $Value)
    if ($Path -notlike 'HKCU:\*') { throw 'Unexpected Set-Item target.' }
    $state.RegistryCalls++
}
function New-ItemProperty {
    param([string]$Path, [string]$Name, $Value, [switch]$Force)
    if ($Path -notlike 'HKCU:\*') { throw 'Unexpected registry property target.' }
    $state.RegistryCalls++
}

try {
    foreach ($case in @('source', 'release')) {
        $state.Calls.Clear(); $state.RegistryCalls = 0
        $directory = Join-Path $testRoot $case
        $agentSource = Join-Path $directory 'source/local_cad_agent'
        [IO.Directory]::CreateDirectory($agentSource) | Out-Null
        [IO.File]::Copy($originalInstaller, (Join-Path $agentSource 'install-windows.ps1'))
        Copy-Item -LiteralPath (Join-Path (Split-Path $originalInstaller) 'register-preprocessor.ps1') -Destination $agentSource
        if ($case -eq 'source') {
            foreach ($relative in @(
                'preprocessor/modelryx_freecad_preprocessor.py',
                'shared/modelryx_freecad_shared.py',
                'converter/fcstd_to_modelryx_glb.py'
            )) {
                $fixture = Join-Path $directory "source/workers/freecad/$relative"
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($fixture)) | Out-Null
                [IO.File]::WriteAllText($fixture, 'fixture')
            }
            foreach ($source in $converterFiles) {
                Copy-Item -LiteralPath $source.FullName -Destination (Join-Path $directory "source/workers/freecad/converter/$($source.Name)") -Force
            }
        }
        if ($case -eq 'release') {
            $release = Join-Path $agentSource 'artifacts/win-x64'
            [IO.Directory]::CreateDirectory($release) | Out-Null
            [IO.File]::WriteAllText((Join-Path $release 'Modelryx.LocalCad.Agent.exe'), 'prebuilt marker')
            $prebuiltFreeCad = Join-Path $release 'preprocessors/freecad'
            [IO.Directory]::CreateDirectory($prebuiltFreeCad) | Out-Null
            foreach ($source in $converterFiles) {
                Copy-Item -LiteralPath $source.FullName -Destination $prebuiltFreeCad
            }
            [IO.File]::WriteAllText((Join-Path $prebuiltFreeCad 'modelryx_freecad_preprocessor.py'), 'preprocessor marker')
            [IO.File]::WriteAllText((Join-Path $prebuiltFreeCad 'modelryx_freecad_shared.py'), 'shared marker')
        }
        $install = Join-Path $directory 'install'
        & (Join-Path $agentSource 'install-windows.ps1') -InstallDirectory $install
        foreach ($source in $converterFiles) {
            $installed = Join-Path $install "app/preprocessors/freecad/$($source.Name)"
            if (-not (Test-Path -LiteralPath $installed) -or
                (Get-FileHash -LiteralPath $installed).Hash -ne (Get-FileHash -LiteralPath $source.FullName).Hash) {
                throw "The FreeCAD converter file $($source.Name) was not installed correctly."
            }
        }
        $settings = Get-Content -LiteralPath (Join-Path $install 'app/settings.json') -Raw | ConvertFrom-Json
        if (-not (Test-Path -LiteralPath $settings.FreeCadPreprocessorScript) -or
            -not (Test-Path -LiteralPath $settings.FreeCadConverterScript)) {
            throw "FreeCAD paths are missing from settings for $case."
        }
        $expectedBuilds = if ($case -eq 'source') { 1 } else { 0 }
        if ($state.Calls.Count -ne $expectedBuilds) { throw "Unexpected publish count for $case" }
        if ($state.RegistryCalls -eq 0) { throw 'Protocol registration was not exercised.' }
        if ($case -eq 'source') {
            $settings.ExternalPreprocessors = @([pscustomobject]@{ Id = 'example'; Executable = 'example.exe' })
            $settings.AllowedOrigins = @('http://192.168.50.53:5173')
            $settings | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $install 'app/settings.json') -Encoding UTF8
            & (Join-Path $agentSource 'install-windows.ps1') -InstallDirectory $install
            $reinstalledSettings = Get-Content -LiteralPath (Join-Path $install 'app/settings.json') -Raw | ConvertFrom-Json
            if (@($reinstalledSettings.ExternalPreprocessors)[0].Id -ne 'example') { throw 'External registration was not preserved.' }
            if (@($reinstalledSettings.AllowedOrigins) -notcontains 'http://192.168.50.53:5173') {
                throw 'The installer did not preserve the configured web origin.'
            }
        }
        Write-Output "PASS: $case ($expectedBuilds publishes)"
    }
}
finally {
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if (-not $resolved.StartsWith($tempParent + '\', [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolved) -notlike 'modelryx-installer-*') { throw 'Refusing cleanup outside the test-owned directory.' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
