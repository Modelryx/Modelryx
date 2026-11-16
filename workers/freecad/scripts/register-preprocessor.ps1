[CmdletBinding()]
param(
    [string]$WorkerId = 'freecad',
    [string]$Version,
    [string]$PreprocessorDirectory = (Join-Path $PSScriptRoot '..\artifacts\preprocessor'),
    [string]$InstallDirectory = "$env:LOCALAPPDATA\Modelryx\LocalCadAgent"
)
$ErrorActionPreference = 'Stop'
$preprocessor = Join-Path $PreprocessorDirectory 'Modelryx.FreeCAD.Preprocessor.exe'
if (-not (Test-Path -LiteralPath $preprocessor -PathType Leaf)) {
    throw "Preprocessor executable not found at '$preprocessor'. Build or publish the FreeCAD preprocessor first."
}
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = [Diagnostics.FileVersionInfo]::GetVersionInfo($preprocessor).ProductVersion
    if ([string]::IsNullOrWhiteSpace($Version)) { throw 'Could not determine the preprocessor version; provide -Version.' }
    $Version = ($Version -split '\+')[0]
}
$installer = Join-Path $InstallDirectory 'app\register-preprocessor.ps1'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw 'Install or update the Modelryx Local CAD Agent before registering this preprocessor.'
}
& $installer -WorkerId $WorkerId -CadSystem 'freecad' -Version $Version `
    -Extensions @('.fcstd') `
    -SourceDirectory $PreprocessorDirectory -Executable 'Modelryx.FreeCAD.Preprocessor.exe' `
    -InstallDirectory $InstallDirectory
