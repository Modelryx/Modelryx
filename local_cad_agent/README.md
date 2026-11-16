# Local CAD Agent

The agent opens a native file dialog, runs dependency analysis on the user's computer,
and returns an isolated CAD package to the browser. It listens on `127.0.0.1:47832`.
FreeCAD preprocessing is included. Other CAD systems use independently installed preprocessors
registered against an external worker ID; this repository contains no vendor SDK integration.

## Installation

On Windows, run `./local_cad_agent/install-windows.ps1`. A release package uses its prebuilt,
self-contained executable. Source installations require the .NET 8 SDK; `-BuildFromSource`
forces rebuilding the agent. Set `-AllowedOrigins` to your frontend's origins and
`-FreeCadCommand` to the FreeCAD executable if it is not found automatically.

On Linux, use `./local_cad_agent/install-linux.sh`. Install FreeCAD and a supported native
file dialog utility as described by that script. Windows-only external runtimes must be
installed and supported by their own preprocessor distributions.

The installer preserves `ExternalPreprocessors` and `AllowedOrigins` in `app/settings.json`
on reinstall. Additional preprocessors are not built or discovered automatically.

## External preprocessors

First register the remote worker in **Administrator > External CAD Workers**. The worker
advertises its CAD system, source extensions, document kinds, and output roles. The local
file dialog uses those declarations, including when multiple systems share an extension.

Use the installed `app/register-preprocessor.ps1` with `WorkerId`, `CadSystem`, `Version`,
`Extensions`, `SourceDirectory`, and `Executable`. Copy the complete executable distribution.
The worker ID, CAD system, and extension must match the server registration.

The executable receives two arguments: `<absolute-source-path> <output-directory>`.
It owns native runtime discovery, environment setup, reference resolution, package isolation,
and cleanup of native processes. It returns the neutral dependency-report contract documented
in the [worker SDK](../worker-sdk/README.md).

The agent validates the package and refuses unsupported formats or ambiguous registrations.
A process cancelled by the agent is terminated with its child process tree. External runtime
processes started independently of that tree remain the preprocessor's responsibility.

## Configuration

`settings.json` is loaded from the application directory or the per-user configuration directory.
Supported settings are `FreeCadCommand`, `FreeCadPreprocessorScript`, `FreeCadConverterScript`,
`ExternalPreprocessors`, and `AllowedOrigins`. FreeCAD paths can also be configured with
`MODELRYX_FREECAD_CMD`, `MODELRYX_FREECAD_PREPROCESSOR`, and `MODELRYX_FREECAD_CONVERTER`.
`MODELRYX_ALLOWED_ORIGINS` overrides configured web origins.

## Security and verification

Only configured web origins and origin-free local clients can use the loopback API. File
selection requires the native dialog; packages must stay inside the agent workspace and
pass isolation and path validation. Packages are removed after download or on expiry.

```powershell
dotnet run --project local_cad_agent/tests/Modelryx.LocalCad.Agent.Tests
./local_cad_agent/tests/Test-WindowsInstaller.ps1
```

These checks exercise generic system routing, shared extensions, ambiguous registrations,
external executable dispatch, source installation, bundled releases, and reinstall settings.
