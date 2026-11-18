# Modelryx FreeCAD worker

Native document closure waits up to 30 seconds for pending TechDraw tasks before
freeing drawing or referenced base objects. A timeout fails the operation; the
isolated worker deadline remains responsible for terminating stalled native work.
This applies to extraction and dependency preprocessing.

## Reference external worker

This is the in-repository reference for designing other CAD workers. Native extraction and
conversion stay under `workers/freecad`; the server owns Items, revisions, authorization,
publication and managed BOM relationships. Worker and local preprocessor are separate deployments.

Register this external HTTP service in **Administrator > External CAD Workers**, with CAD system
`freecad` and the same internal worker key. Multiple matching registrations are an error.
Extraction, metadata, geometry and previews run on this server. The main platform contains no
embedded FreeCAD service. Controlled CAD editing sessions are disabled in this release; future
editing support will use external workers. STEP retains its separate service.

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
The URL-based external PFG publication path is also supported.

### Local preprocessor registration

This client installation is required for the browser's **Open CAD File** action
with `.FCStd` files. Install it on each machine running the browser and holding the
CAD files. Starting the server does not install the local executable or Local CAD Agent.
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
An enabled registration or reachable configured external worker URL lets the dialog discover
FreeCAD support. Restart the Local CAD Agent after registering the executable.
`artifacts/preprocessor` contains the executable host, Python entry point, preprocessor,
and one shared module. It contains no converter or extraction scripts. Copy the complete
distribution. Set `MODELRYX_FREECAD_CMD` to FreeCAD's Python executable when discovery fails.
Local preprocessing needs FreeCAD, but not NumPy, trimesh or Pillow.

The host starts the native Python processes, enforces a timeout, forwards stdout/stderr, and
propagates failure. `preprocessor/local_preprocessor.py` discovers and rebases package copies,
and validates isolated reopening. Metadata extraction and geometry-based duplicate assessment
occur on the external server after upload.
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
system; keep platform publication and database orchestration outside native adapters. Run the
SDK conformance probe against a licensed/native fixture as described there. Re-register after
changing capabilities. The [new worker guide](../../docs/workers/new-cad-worker-guide.md)
describes ownership, native evidence and generic publication boundaries.

## Standalone HTTP service

The standalone image implements the read-only part and assembly profile in the
[worker protocol](../../worker-sdk/README.md). It uses its own job volume and does not require the
Modelryx server image, database, object storage, or STEP service.

FreeCAD builds and runs from its own directory. Configure its own environment file first:

```powershell
Copy-Item workers/freecad/.env.example workers/freecad/.env
# Set the worker key in workers/freecad/.env to match the Modelryx server.
.\workers\freecad\scripts\start-freecad-server.ps1
```

The script reads `workers/freecad/.env` through Docker Compose, builds the standalone image, starts it
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
external worker, independently of the main platform. To view logs
or stop it without removing retained jobs:

```powershell
docker compose -f workers/freecad/compose.standalone.yaml --env-file workers/freecad/.env logs -f freecad
docker compose -f workers/freecad/compose.standalone.yaml --env-file workers/freecad/.env stop freecad
```

Build using only `workers/freecad` as the Docker context. You can copy this directory to an
independent worker checkout; the platform server and live SDK sources are not required. Set `MODELRYX_INTERNAL_WORKER_KEY` in your
environment before starting the container.

```sh
docker build -t modelryx-freecad-standalone workers/freecad
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

Administrators may also set `MODELRYX_FREECAD_WORKER_URL` and
`MODELRYX_FREECAD_PREPROCESSOR_URL` to the external service, or configure URLs in Operations.
Saved URLs override environment defaults. An empty worker URL provides no conversion service;
an empty preprocessor URL disables server-side analysis. The local file dialog discovers formats from the registered or configured server capabilities. The general orchestrator reads settings each polling cycle.

The worker's adapter version must match the server's `MODELRYX_CONVERTER_VERSION` so cached
conversions and published artifact provenance use the configured converter version.

The opt-in server test `server/tests/test_freecad_remote_live.py` exercises managed publication
using a real standalone worker. Generate its linked-assembly inputs with
`workers/freecad/tests/generate_remote_fixture.py` in the FreeCAD runtime. Run the test against
the isolated Modelryx test database with `MODELRYX_TEST_FREECAD_WORKER_URL` pointing to the
worker, `MODELRYX_TEST_FREECAD_FIXTURES` pointing to the generated files, and
`MODELRYX_INTERNAL_WORKER_KEY` matching the worker. It checks the preview, published artifacts,
and the committed child revision reference.

## Independent build and CI

`Dockerfile` builds the external HTTP runtime by default. Its `native-tests` target adds only
worker-owned tests and contract snapshots. It installs no platform server package and receives
no files outside this directory. Build native validation with:

```powershell
docker build --target native-tests -t modelryx-freecad-tests workers/freecad
docker run --rm modelryx-freecad-tests python converter/golden_integration_test.py
docker run --rm modelryx-freecad-tests python preprocessor/dependency_integration_test.py
docker run --rm modelryx-freecad-tests python converter/production_fixture_test.py
```

FreeCAD CI is triggered by non-Markdown changes under `workers/freecad/` or `worker-sdk/`,
or manually. Platform, frontend and workflow-file-only changes do not trigger it. The separate manual
compatibility workflow tests candidate FreeCAD runtimes. Keep platform checks required globally;
path-filtered FreeCAD checks should not be globally required for unrelated pull requests.

The worker owns versioned SDK and PartMetadata snapshots with hashes and license provenance.
Adopt upstream updates explicitly using [the snapshot refresh workflow](contracts/README.md).
Native metadata and execution fixtures remain here; platform CAD plan selection and publication
validation belong to platform tests. Unit tests from this directory need only the worker files:

```powershell
python -m pip install ./vendor/worker-sdk -r standalone/requirements.txt -r requirements-test.txt
python -m pytest -q tests
```

Key directories:

- `converter/` — server-side conversion, metadata normalization, and reserved future editing code
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
```

The production FreeCAD worker does not perform dependency discovery or package rewriting. It
converts the isolated package received from Modelryx. Only format provenance and assembly-model
inspection are shared through `shared/modelryx_freecad_shared.py`.

Self-contained FreeCAD parts and assemblies remain single-file packages. Workbench-specific link
semantics and native compatibility fixtures remain in the parity backlog.

The shared contract, path mapping rules, security boundary, test matrix, and implementation order
are documented in
[`../../docs/workers/new-cad-worker-guide.md#source-contract`](../../docs/workers/new-cad-worker-guide.md#source-contract).


### Format discovery

Open CAD File reads `/api/v1/workers/external-formats`. The server gets format names, versions,
extensions and document kinds from authenticated worker capabilities. Enabled external
registrations take precedence; a configured external FreeCAD worker URL is also discovered,
so URL-only setups do not need a duplicate registration. No FreeCAD extension is invented by
the local file picker. The configured server must be reachable and advertise a valid worker
contract. The local preprocessor registration must match the returned worker ID (`freecad` for
the standard URL-only FreeCAD service). Rebuild/reinstall and restart the local agent after
updating its source. Local runtime and preprocessor installation are still required.


### Native CI resource limits and termination diagnostics

Each native release gate has its own CI step and runs with 4 GiB RAM, 8 GiB combined RAM/swap,
2 CPUs, and one BLAS/OpenMP thread. Low-memory GitHub runners provision an additional 4 GiB
swap file when available RAM is below 6 GiB and existing swap is below 4 GiB. Job cleanup removes
only that job-owned swap file. These settings apply to CI, not the production worker.

The gate wrapper retains Docker's exit status and `OOMKilled` flag before removing the
container. Exit 137 means SIGKILL; it does not by itself establish an out-of-memory failure.
On failure the workflow also reports host memory, disk and recent kernel diagnostics.
The gate's original failure is preserved; no native test is skipped or retried automatically.
