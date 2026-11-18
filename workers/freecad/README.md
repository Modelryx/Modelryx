# Modelryx FreeCAD worker

Native document closure waits up to 30 seconds for pending TechDraw tasks before
freeing drawing or referenced base objects. A timeout fails the operation; the
isolated worker deadline remains responsible for terminating stalled native work.
This applies to extraction, controlled edits, and dependency preprocessing.

## Reference external worker

This is the in-repository reference for designing other CAD workers. Native extraction and
conversion stay under `workers/freecad`; the server owns Items, revisions, authorization,
publication and managed BOM relationships. The embedded FreeCAD processing role remains supported.

An enabled matching registration in **Administrator → External CAD Workers** takes precedence
for new `.FCStd` uploads, regeneration and copied revisions. Register this service's URL with CAD
system `freecad` and the same internal worker key. Disable or remove that registration to use
the existing FreeCAD path: its configured standalone URL, or embedded conversion if the URL is
empty. Existing claimed jobs keep their selected job type. Multiple matching registrations are
an error; Modelryx does not silently select a worker or fall back after an external job fails.
STEP remains built in. FreeCAD CAD sessions continue using the embedded runtime.

The authenticated capabilities endpoint advertises name/version, part/assembly kinds, `.fcstd`,
dependency analysis and these semantic output roles:

| Role | Media type | Purpose |
| --- | --- | --- |
| `native-snapshot` | `application/json` | Loss-minimized native extraction evidence |
| `part-metadata` | `application/json` | Normalized PartMetadata v1 |
| `model-preview` | `application/json` | Viewer navigation and occurrence IDs |
| `glb` | `model/gltf-binary` | Browser geometry |
| `modelryx-preview` | `application/zip` | Existing PFG profile for the built-in remote client |

Generic Modelryx jobs require the first three roles and prefer GLB. Outputs include hashes,
sizes and generator/source provenance. Only requested roles are returned. GLB and model preview
are bounded, validated exports from the same PFG conversion, preserving occurrence identity.
The existing embedded and URL-based PFG publication paths are retained.

### Local preprocessor registration

This client installation is required for the browser's **Open CAD File** action
with `.FCStd` files. Install it on each machine running the browser and holding the
CAD files. The server's Docker `freecad-preprocessor` service is a separate
component and does not install the local executable or Local CAD Agent.
See the [complete client setup](../../DEVELOPMENT.md#enable-open-cad-file-for-freecad).

The generic Local CAD Agent launches a registered executable with two arguments: source path and
an empty output directory. It reads a JSON dependency report from stdout. Build and register the
FreeCAD implementation on each Windows client after installing FreeCAD, .NET 8 and the agent:

```powershell
.\workers\freecad\scripts\build-preprocessor.ps1
.\workers\freecad\scripts\register-preprocessor.ps1 -WorkerId freecad
```

Use the exact worker ID assigned in administration. `-InstallDirectory` selects another agent
installation; `-PreprocessorDirectory` and `-Version` support a published distribution.
The server registration must be enabled so the local file dialog discovers FreeCAD
support. Restart the Local CAD Agent after registering the executable.
`artifacts/preprocessor` contains the executable host, Python entry point, preprocessor,
converter and shared modules. Copy the complete distribution. Set `MODELRYX_FREECAD_CMD` to
FreeCAD's Python executable when automatic discovery is insufficient. The runtime also needs
the converter's Python dependencies (NumPy, trimesh and Pillow).

The host starts the native Python processes, enforces a timeout, forwards stdout/stderr, and
propagates failure. `preprocessor/local_preprocessor.py` discovers and rebases package copies,
validates isolated reopening and extracts duplicate-check metadata from the rebased package.
It returns missing references explicitly and never saves the original native documents.
The worker and client preprocessor are independent installations; clients need no worker key.

### Use this as a design example

- `standalone/api.py`: authenticated capabilities, bounded multipart uploads and job/output routes.
- `standalone/conversion.py`: SDK-free request validation and safe source reconstruction.
- `standalone/jobs.py`: durable queue, replay/conflict checks, cancellation, expiry and manifests.
- `modelryx_freecad_adapter.py`: disposable native conversion process boundary.
- `preprocessor-host/` and `preprocessor/local_preprocessor.py`: generic local agent integration.
- `tests/test_standalone_api.py`: generic and legacy profile contract tests without FreeCAD.

Implement the [worker SDK protocol](../../worker-sdk/README.md) when adapting another CAD
system; do not copy FreeCAD's server/database orchestration into a vendor adapter. Run the
SDK conformance probe against a licensed/native fixture as described there. Re-register after
changing capabilities. The [new worker guide](../../docs/workers/new-cad-worker-guide.md)
describes ownership, native evidence and generic publication boundaries.

## Standalone HTTP service

The standalone image implements the read-only part and assembly profile in the
[worker protocol](../../worker-sdk/README.md). It uses its own job volume and does not require the
Modelryx server image, database, object storage, or STEP service.

From the repository root, start it with PowerShell:

```powershell
.\workers\freecad\scripts\start-freecad-server.ps1
```

The script reads the root `.env` through Docker Compose, builds the standalone image, starts it
in the background, and waits for readiness. Set `MODELRYX_INTERNAL_WORKER_KEY` to the same key
used by Modelryx. Use `-EnvFile` for another environment file, `-Port 8091` for another host port,
or `-SkipBuild` to reuse an existing image. The default listener is `127.0.0.1:8090`.

If Modelryx runs in Docker Desktop on the same machine:

```powershell
.\workers\freecad\scripts\start-freecad-server.ps1 -ListenAddress 0.0.0.0
```

Register `http://host.docker.internal:8090` in **Administrator → External CAD Workers**.
For a remote server, use the worker host's reachable address and the private-network/TLS
deployment guidance below. Starting the service does not register it automatically.

This uses `compose.standalone.yaml` and its own persistent job volume. It starts only the
external worker, independently of the main server and embedded FreeCAD services. To view logs
or stop it without removing retained jobs:

```powershell
docker compose -f workers/freecad/compose.standalone.yaml --env-file .env logs -f freecad
docker compose -f workers/freecad/compose.standalone.yaml --env-file .env stop freecad
```

Build from the repository root on the worker host. Set `MODELRYX_INTERNAL_WORKER_KEY` in your
environment before starting the container.

```sh
docker build -f workers/freecad/Dockerfile.standalone -t modelryx-freecad-standalone .
docker run --rm --name modelryx-freecad-standalone \
  --env MODELRYX_INTERNAL_WORKER_KEY \
  --mount source=modelryx-freecad-jobs,target=/data/jobs \
  --memory 8g --pids-limit 512 \
  -p 127.0.0.1:8090:8090 modelryx-freecad-standalone
```

On a dedicated host, put the loopback listener behind a TLS reverse proxy reachable by the
Modelryx server and restrict access to trusted clients. Set a request timeout and disk quota
appropriate for CAD uploads and retained outputs. Run one worker process per job volume. The
worker runs one conversion at a time, admits up to four active or queued jobs, and retains terminal
job records and successful outputs for 24 hours. Use the same volume when restarting the container.

To verify a deployment, query `GET /ready` and the authenticated `GET /api/v1/capabilities`.
Submit the protocol's multipart `request` and `source` file parts, then poll the returned
`Location` and download the advertised outputs. Use `application/octet-stream` for a single
FCStd file and `application/zip` for a dependency package. The declared size and SHA-256 must
describe the exact uploaded bytes. Include a future `expiresAt` in the request.

```sh
curl -i http://127.0.0.1:8090/api/v1/jobs \
  -H "X-Modelryx-Worker-Key: $MODELRYX_INTERNAL_WORKER_KEY" \
  -F "request=@request.json;type=application/json" \
  -F "source=@assembly.zip;type=application/zip"
```

Without an enabled matching external registration, set `MODELRYX_FREECAD_WORKER_URL` on the Modelryx general worker to the standalone service's
reachable URL and configure the same internal worker key on both sides. Managed FCStd conversion
jobs then use the standalone worker. Leave the URL empty to use the embedded worker described
below. Set `MODELRYX_FREECAD_PREPROCESSOR_URL` on the server to the same standalone URL to use its
authenticated dependency preprocessor. CAD sessions currently use the embedded worker.
Administrators can instead set both URLs under **Administrator → Operations → CAD server settings**.
Saved URLs override the environment defaults. An empty saved worker URL selects embedded processing,
and an empty saved preprocessor URL disables server-side dependency analysis. The general worker picks up
changes on its next polling cycle, while a job already claimed continues with its existing URL.
The availability cards check standalone readiness and authenticated capabilities using the configured
internal worker key. The default Compose deployment runs the embedded FreeCAD image as a separate
`freecad-preprocessor` HTTP service; it is not the standalone conversion service. The old `MODELRYX_CAD_ANALYZER_URL` environment variable
remains supported for existing configurations.
The worker's adapter version must match the server's `MODELRYX_CONVERTER_VERSION` so cached
conversions and published artifact provenance use the configured converter version.

The opt-in server test `server/tests/test_freecad_remote_live.py` exercises managed publication
using a real standalone worker. Generate its linked-assembly inputs with
`workers/freecad/tests/generate_remote_fixture.py` in the FreeCAD runtime. Run the test against
the isolated Modelryx test database with `MODELRYX_TEST_FREECAD_WORKER_URL` pointing to the
worker, `MODELRYX_TEST_FREECAD_FIXTURES` pointing to the generated files, and
`MODELRYX_INTERNAL_WORKER_KEY` matching the worker. It checks the preview, published artifacts,
and the committed child revision reference.

## Embedded worker

This image runs the dedicated `freecad` processing role. It shares the server's PostgreSQL
`ProcessingJob` queue and object storage, but claims only `FCSTD_TO_MODELRYX` work and
FreeCAD-targeted CAD sessions. Generic staging, STEP, external CAD, and bundle jobs are
handled by the lightweight server `general-worker` service.

Standalone FreeCAD runtime for Modelryx. It retains the rich direct FCStd-to-GLB/PFG path and
keeps optional command-line utilities separate from the production converter.

Build from the `Modelryx` parent directory because the image installs the server's job orchestration
package while keeping FreeCAD conversion code outside the server project:

```powershell
docker build -f workers\freecad\Dockerfile -t modelryx-freecad-worker .
```

The production image defaults to FreeCAD 1.1.3. The manually dispatched FreeCAD compatibility
workflow also builds previous and newer candidate runtimes and runs the golden, dependency, and
production fixture suites against them.
Candidate lanes are informational and do not change the worker's configured compatibility policy;
package availability and fixture failures must be reviewed before supporting another version.
The production fixture retains its version assertion by default; `--compatibility-candidate` runs
the same behavioral checks on a candidate without asserting it is supported.

Key directories:

- `converter/` — production FCStd conversion, metadata normalization, and controlled CAD execution
- `preprocessor/` — local-only root selection, dependency discovery, rebinding, and packaging
- `shared/` — low-level FCStd provenance and assembly-model inspection used by both processes
- `tools/` — optional check-in and shared STEP-service command-line utilities

Set `MODELRYX_FREECAD_STAGE_TIMEOUT_SECONDS` to a positive number to apply a time limit
to each conversion stage, including startup. The clock resets when the converter reports a
new stage. The existing overall job timeout still applies. Leave it unset to retain only the
overall timeout; select a limit appropriate for the expected model sizes.

## Assembly evidence

Native joint references include `occurrence_resolution`: `resolved` carries an occurrence ID,
`ambiguous` carries the candidate IDs, and `unresolved` preserves the original reference.
Resolution uses package-qualified document identities and explicit nested object paths.
Structural occurrences are distinguished from navigation copies claimed beneath features or joints;
navigation copies are excluded from joint resolution. Repeated definitions are never resolved by
choosing the first matching component. Incomplete mappings keep assembly constraints partial.
Ambiguous candidate lists are limited to 100 IDs; truncated results retain the total candidate count.

Link arrays include native definition evidence for `LinkCopyOnChange` and, for expanded arrays,
each element's linked definition. Array BOM occurrences require a verified uniform definition.
An expanded array with an owned copy can be counted when native ownership, matching configuration
selectors, element targets, transforms, visibility and suppression are complete; the copied
definition receives its own identity. Other configured, heterogeneous, or unreadable definitions
remain unresolved. Collapsed arrays retain
element transforms and visibility, but per-element suppression is unknown: collapsing native link
elements can discard their suppression extensions. Visibility is not treated as suppression.
`LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN` identifies the array and counts unknown and uninspected
elements; downstream consumers should check this diagnostic before relying on array quantities
or combined-shape measurements.
Expanded element evidence retains the actual linked object, its document identity, and the native
copy-on-change policy even when a configured array cannot yet produce authoritative BOM rows.
The native copy-on-change source and storage-group references are retained as evidence; the
generated storage group is excluded from occurrence traversal so it is not counted as another part.
FreeCAD's combined array shape can include suppressed elements. For expanded arrays with complete
native suppression and visibility evidence, the worker builds visible geometry from individual
unsuppressed elements. Per-face colors are mapped to that filtered compound only when bounded
native geometry comparisons uniquely match every retained face;
ambiguous, failed or larger mappings are reported as partial. Other array geometry remains partial.
When array element geometry cannot be verified, aggregate mass and center of mass are omitted
instead of being derived from the potentially inaccurate combined shape; available material and
density evidence remains available.

Joint `degrees_of_freedom` describes the nominal joint type, identified by
`degrees_of_freedom_basis: nominal_joint_type`. It is not a solver measurement;
`solver_status` remains unavailable until native solver evidence can be inspected. Built-in
assemblies emit `ASSEMBLY_SOLVER_OBSERVATION_UNAVAILABLE` with the assembly object name.
Metadata extraction does not explicitly call the mutating assembly solver: its return code alone
does not identify observed freedom or establish that every constraint is satisfied. The existing
document recompute can itself invoke FreeCAD's solver and change in-memory placements; extraction
does not save those changes back to the source document.

## Root-document dependency packaging

`preprocessor/modelryx_freecad_preprocessor.py` is the local-only FreeCAD preprocessor for
Modelryx's single-root upload contract. It opens one `.FCStd` read-only, classifies it from document content, traverses
native external link properties, packages only reachable documents, maps outside dependencies to
`externalN`, and reports missing links with the referring document and a fix suggestion. Packaging
is performed on temporary copies: resolved native links are rebound to the copied documents and the
root is reopened before the ZIP is returned. Analysis never saves the original source documents.

The local CAD selection service invokes it through FreeCAD's bundled Python runtime. Configure the
Windows agent when automatic discovery is not sufficient:

```powershell
$env:MODELRYX_FREECAD_CMD = "C:\Program Files\FreeCAD 1.1\bin\python.exe"
$env:MODELRYX_FREECAD_PREPROCESSOR = "D:\project1\Modelryx\workers\freecad\preprocessor\modelryx_freecad_preprocessor.py"
```

The production FreeCAD worker does not perform dependency discovery or package rewriting. It
converts the isolated package received from Modelryx. Only format provenance and assembly-model
inspection are shared through `shared/modelryx_freecad_shared.py`.

Self-contained FreeCAD parts and assemblies remain single-file packages. Workbench-specific link
semantics and native compatibility fixtures remain in the parity backlog.

The shared contract, path mapping rules, security boundary, test matrix, and implementation order
are documented in
[`../../docs/workers/new-cad-worker-guide.md#source-contract`](../../docs/workers/new-cad-worker-guide.md#source-contract).
