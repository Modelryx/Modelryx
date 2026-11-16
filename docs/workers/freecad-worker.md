# FreeCAD worker

FreeCAD is the in-repository reference implementation for other external CAD workers.
Its native converter and metadata extraction remain outside the server package. Modelryx
continues to support embedded FreeCAD processing and CAD sessions.

## Selecting a deployment

| Configuration | `.FCStd` processing |
| --- | --- |
| Enabled matching External CAD Worker registration | Generic external job and validated publication |
| No registration, dedicated FreeCAD worker URL configured | Existing remote PFG conversion/publication |
| Neither registration nor dedicated URL | Existing embedded FreeCAD role |

Registration is opt-in. Disabled registrations do not override the existing paths. Ambiguous
registrations fail explicitly, and failures from a selected external worker do not silently
switch to embedded processing. STEP remains built in. CAD sessions retain the embedded runtime.

See the [worker README](../../workers/freecad/README.md) for Docker deployment, worker registration,
local executable preprocessor installation, source ownership and a map of the reference code.
The [worker SDK](../../worker-sdk/README.md) is the authoritative language-neutral HTTP contract.

## External contract

Capabilities advertise `freecad`, `.fcstd`, part/assembly document kinds, a display name/version
and dependency analysis. Jobs accept authenticated multipart files named `request` and `source`.
The JSON request declares `source.sha256`, `source.size`, `source.rootPath` and `expiresAt`.
Use `application/zip` for dependency packages and `application/octet-stream` for native FCStd.

Jobs progress through `Queued`, `Reconstructing`, `Extracting` and `Validating` to `ReadyToPublish`
or `Partial`; failure, cancellation and expiry are terminal. Replaying the same job identity
and request returns the retained record; conflicting reuse is rejected. Cancellation is idempotent.
The service uses a durable single-process queue, bounded workspaces and 24-hour terminal retention.

| Output role | Content |
| --- | --- |
| `native-snapshot` | Native CAD extraction JSON |
| `part-metadata` | PartMetadata v1 JSON |
| `model-preview` | Occurrence/navigation JSON |
| `glb` | Browser geometry |
| `modelryx-preview` | PFG archive used by the existing dedicated FreeCAD client |

Generic jobs require native snapshot, normalized metadata and model preview, and prefer GLB.
Each requested output has a size, hash, job-scoped download URL and provenance. The standalone
service derives the generic viewer outputs from the same PFG conversion used by the embedded path.
Server-side publication validates identity, hashes, schemas and preview structure before making
artifacts visible. Dependency analysis uses the authenticated `/analyze` extension.

## Local preprocessing

The separately installed `Modelryx.FreeCAD.Preprocessor` executable follows the generic Local CAD
Agent contract: source and output directory arguments, a package and JSON dependency report.
It runs the existing FreeCAD dependency packager on native copies, checks isolated reopening,
and obtains local duplicate-check metadata from the rebased package. Missing links are reported
with their referring document. The original files remain authoritative and are not saved.

Other CAD workers can copy these boundaries and implement their own native adapter. They should
reuse the protocol and conformance checks, without adding vendor SDKs to the Modelryx server.
