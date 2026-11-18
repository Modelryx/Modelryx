# FreeCAD worker

FreeCAD runs only as a separately deployed HTTP server. It performs native extraction,
metadata normalization, geometry and preview generation, without direct platform database
or object-volume access. STEP uses its separate geometry service.

| Configuration | Conversion route |
| --- | --- |
| Enabled matching external registration | Registered FreeCAD HTTP worker |
| No matching registration, external FreeCAD URL configured | HTTP worker discovered through capabilities |
| Neither | FreeCAD processing unavailable |

Multiple matching registrations are an error. A failed external job does not run locally.
Controlled CAD editing sessions are disabled in both the UI and API for this release.
Future editing support will belong to external workers.

Open CAD File requires the generic Local CAD Agent and an independently installed FreeCAD
preprocessor on each client. The preprocessor resolves references, rebases copies, verifies
isolated reopening and packages sources; it does not extract metadata or geometry. Its package
contains only the executable host and three required Python files, with no converter modules.
All extraction and geometry-based duplicate assessment occur on the server after upload.

See [runtime and client setup](../../workers/freecad/README.md),
[development setup](../../DEVELOPMENT.md#enable-open-cad-file-for-freecad), and
[the worker protocol](../../worker-sdk/README.md).


### Format discovery

Open CAD File reads `/api/v1/workers/external-formats`. The server gets format names, versions,
extensions and document kinds from authenticated worker capabilities. Enabled external
registrations take precedence; a configured external FreeCAD worker URL is also discovered,
so URL-only setups do not need a duplicate registration. No FreeCAD extension is invented by
the local file picker. The configured server must be reachable and advertise a valid worker
contract. The local preprocessor registration must match the returned worker ID (`freecad` for
the standard URL-only FreeCAD service). Rebuild/reinstall and restart the local agent after
updating its source. Local runtime and preprocessor installation are still required.
