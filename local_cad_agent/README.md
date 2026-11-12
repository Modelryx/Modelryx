# Modelryx Local CAD Agent

This standalone application owns the native **Open CAD File** dialog and dependency analysis.
The browser selects one root document only. The agent opens it read-only through the installed CAD
system, follows native references, and exposes a minimal package on loopback for one download.

The application targets Windows and Linux and is published self-contained, so end users do not
need to install .NET or a Rust toolchain.

Release packages include the local agent and version-matched local preprocessors. The
installer does not compile source code and does not require a .NET SDK on the user's machine.

## Install

Windows (per-user installation and `modelryx-cad://` protocol registration):

```powershell
.\install-windows.ps1
```

When run from a source checkout without a prebuilt agent, this command builds the agent and
automatically builds the NX preprocessor if the configured NX installation is present. It does not
automatically build Autodesk preprocessors. A prebuilt release installation still requires no SDK.

Repository developers can explicitly rebuild the CAD preprocessors during installation:

```powershell
.\install-windows.ps1 -BuildPreprocessorsFromSource
```

To build the updated agent and only the NX preprocessor from this checkout (without building the
Autodesk preprocessors), use the .NET 8 SDK and the installed NX2506 managed API:

```powershell
.\install-windows.ps1 -BuildNxPreprocessorFromSource `
  -NxDirectory 'C:\Program Files\Siemens\NX2506'
```

Release installs consume a prebuilt `preprocessors\nx\Modelryx.NX.Preprocessor.exe` and its runtime
files when included in the package. A release without that preprocessor reports NX as unavailable;
it does not silently run an SDK-free discovery substitute.

To pin a non-standard FreeCAD installation, pass its bundled Python executable:

```powershell
.\install-windows.ps1 `
  -FreeCadCommand "D:\Apps\FreeCAD\bin\python.exe"
```

This developer-only mode requires the SDK used by the installed CAD release. In particular,
AutoCAD 2027 preprocessors target `net10.0-windows` and require the .NET 10 SDK. This requirement does
not apply when installing a prebuilt release package. The AutoCAD framework and SDK directory can
be selected explicitly for a different supported release:

```powershell
.\install-windows.ps1 -BuildPreprocessorsFromSource `
  -AutoCadTargetFramework net8.0-windows `
  -AutoCadSdkDir "C:\Program Files\Autodesk\AutoCAD 2026"
```

Linux (per-user desktop/protocol registration):

```sh
chmod +x ./install-linux.sh
./install-linux.sh
```

Linux requires `zenity` or `kdialog` for the desktop-native file picker. The picker is launched as
a modal top-level application. Windows creates an invisible topmost owner window and passes it to
the native `IFileOpenDialog`, preventing the dialog from opening behind the browser.

## CAD analyzer configuration

The agent routes by the selected root extension:

| Root format | Platform | Analyzer |
|---|---|---|
| `.FCStd` | Windows/Linux | Installed FreeCAD plus `modelryx_freecad_preprocessor.py` |
| `.iam`, `.ipt`, `.idw`, `.ipn` | Windows | Installed Inventor plus `Modelryx.Inventor.Preprocessor` |
| `.dwg`, `.dxf` | Windows | Installed AutoCAD plus `Modelryx.AutoCAD.Preprocessor` |
| `.prt` | Windows | Installed NX2506 plus native `Modelryx.NX.Preprocessor`; local analysis/package support |

FreeCAD executable discovery covers common Windows and Linux locations. Inventor and AutoCAD are
Windows-only vendor applications; selecting those formats on Linux returns a clear unsupported
platform error. AutoCAD analysis launches its installed Core Console, loads the local preprocessor,
extracts its Xref manifest, and packages only resolved Xrefs. The preprocessors expose the same
neutral `cad-package.zip` and dependency-report contract.

NX parts and assemblies use the existing worker preprocessor for native dependency discovery. The
agent configures `UGII_BASE_DIR`, `NXBIN`, `managed_core` and a private diagnostic directory only in
its child processes. A second, fresh NX process opens an extracted package using package-local
search directories and checks the loaded inventory and occurrence-reference graph. Source documents
are never saved. Nested assemblies and repeated references remain explicit; child CAD documents are
reported as `PART` or `ASSEMBLY`, rather than processing-only support.

Configure `NxDirectory` and `NxPreprocessorExecutable` in the installed `settings.json`, or use
`MODELRYX_NX_DIRECTORY` and `MODELRYX_NX_PREPROCESSOR_EXE`. The default installation probe is
`C:\Program Files\Siemens\NX2506`. Drawing references, incomplete discovery, ambiguous resolution
and packages that resolve outside their isolated workspace are refused. The preprocessor does not
rewrite references in the original documents.

This adds NX analysis to the agent's CLI and loopback selection/package API. The browser can stage
and commit an isolated NX part or assembly package as authoritative `Siemens NX`/`PRT` source,
preserving package-relative dependencies and reference/BOM evidence. Processing is queued after
commit and requires a qualified NX worker or the explicit development-only server opt-in described
in `nx_worker/README.md`. GLB previews also require a compatible STEP service.

Local preprocessors discover, rebind, and package references on the CAD user's workstation. The
Inventor and AutoCAD preprocessors reuse their server adapter/normalizer libraries. The FreeCAD
installation includes the server converter in metadata-only mode, so local preflight and worker
processing share the same traversal and PartMetadata normalizer without creating a GLB locally.
The report binds that metadata to the root document's SHA-256; Modelryx rejects it if the staged
manifest names different bytes. Server workers still regenerate authoritative metadata and
representations after commit.

The installer writes `settings.json` beside the application. Environment variables with the names
previously documented remain supported as explicit administrative overrides. Use the Windows
installer's `-AllowedOrigins` argument for deployed Modelryx web origins.

When the browser is served from another machine, allow the browser's exact origin (scheme, host,
and port) during installation. This is the frontend address, not the API address:

```powershell
.\install-windows.ps1 -AllowedOrigins 'http://192.168.50.53:5173'
```

The installer preserves existing allowed origins when it is rerun without `-AllowedOrigins`. To
replace them deliberately, pass the argument again with the complete approved list. The loopback
agent never listens on the network; this setting only permits that website to call the local agent.

For Inventor dependency analysis, the agent activates the nearest unambiguous `.ipj` found beside
the selected file or in one of its parent directories before opening the document. Set
`MODELRYX_INVENTOR_PROJECT` to an absolute `.ipj` path when a directory contains multiple projects
or the project is stored elsewhere. Inventor's resolved descriptor paths are used to collect files
from project workspaces, workgroups, libraries, and Content Center locations; external files are
copied into isolated `externalN` package directories.

Analysis is non-interactive by default so a hidden Resolve Link dialog cannot stall the agent. As a
diagnostic fallback, set `MODELRYX_INVENTOR_INTERACTIVE_RESOLUTION=1` to show the document and allow
Inventor to prompt for unresolved references. Save corrected references in Inventor, then remove the
override and run the analysis again.

## Local transport and security

- Listens only on `127.0.0.1:47832`. Port `47831` is reserved for the Inventor-specific agent.
- Allows configured Modelryx/localhost origins only.
- Accepts no filesystem path from the browser; every selection requires a native user gesture.
- Returns packages through unguessable one-use identifiers.
- Cancels and cleans up an abandoned analysis when the user starts a new selection after refresh.
- Reports native analysis stages and stops an analyzer with an actionable error if it does not
  finish within the bounded fifteen-minute window required by large legacy assemblies.
- Rejects unsafe ZIP paths, duplicate paths, excessive entries, and packages over 2 GB.
- Deletes downloaded packages and prunes abandoned workspaces after 24 hours.

Additional deployed web origins can be supplied as a comma-separated `MODELRYX_ALLOWED_ORIGINS`
value before starting the application.

## NX verification

Run the SDK-free agent integration tests from the project root:

```powershell
dotnet run --project local_cad_agent/tests/Modelryx.LocalCad.Agent.Tests -c Release
powershell.exe -NoProfile -ExecutionPolicy Bypass -File local_cad_agent/tests/Test-WindowsInstaller.ps1
```

The installer checks use isolated folders and substitute publishing/protocol registration. They
cover source installs with/without NX and prebuilt releases with/without a bundled NX preprocessor.

For an installed-NX check, first build the preprocessor with `NxOpenDirectory` pointing to
`NXBIN\managed_core`, then run with a fresh output workspace:

```powershell
$env:MODELRYX_NX_DIRECTORY = 'C:\Program Files\Siemens\NX2506'
$env:MODELRYX_NX_PREPROCESSOR_EXE = (Resolve-Path 'nx_worker/src/Modelryx.NX.Preprocessor/bin/Release/net8.0/Modelryx.NX.Preprocessor.exe').Path
dotnet run --project local_cad_agent/Modelryx.LocalCad.Agent -c Release -- --analyze `
  'C:\Program Files\Siemens\NX2506\UGOPEN\SampleNXOpenApplications\Java\parts\toycar_assy.prt' `
  'local_cad_agent/artifacts/nx-manual-check'
```

The response should identify `ASSEMBLY`, five native documents, ten reference edges and
`isolated_reopen_validated: true`. The standalone vendor `Selection_UIStyler\Block.prt` should
identify `PART` with one document and no references. The Windows installer and native file dialog
still need manual installation/UI verification; automated native checks use `--analyze`.
