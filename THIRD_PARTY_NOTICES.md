# Third-party software notices

Modelryx's own code is licensed under [Apache-2.0](LICENSE). Third-party software
retains its own copyright notices and license terms; the Modelryx license does
not replace those terms.

Modelryx uses FreeCAD and makes use of facilities provided by **Open CASCADE
Technology (OCCT)** for CAD geometry processing. The STEP worker accesses OCCT
through **pythonocc-core**. These projects are independently maintained.

## Native CAD and runtime components

| Component | Used by | License information | Local license copy |
|---|---|---|---|
| [FreeCAD](https://github.com/FreeCAD/FreeCAD) | Embedded and standalone FreeCAD workers | LGPL; the 1.1.3 release includes LGPL-2.1 text and core source headers allowing LGPL-2.0-or-later. Preserve component-specific terms. | [FreeCAD license](licenses/third-party/FreeCAD-LICENSE.txt) |
| [Open CASCADE Technology](https://github.com/Open-Cascade-SAS/OCCT) | FreeCAD and STEP geometry processing | LGPL-2.1 with the Open CASCADE exception | [LGPL](licenses/third-party/OCCT-LGPL-2.1.txt), [exception](licenses/third-party/OCCT-exception.txt) |
| [pythonocc-core](https://github.com/tpaviot/pythonocc-core) | STEP worker | LGPL-3.0; preserve any applicable per-file later-version permissions | [LGPL-3.0](licenses/third-party/pythonocc-core-LICENSE.txt), [GPL-3.0](licenses/third-party/GPL-3.0.txt) |
| [Psycopg 3](https://github.com/psycopg/psycopg) | Server database driver | LGPL-3.0; binary wheels also contain third-party libraries | [LGPL-3.0](licenses/third-party/Psycopg-LICENSE.txt), [GPL-3.0](licenses/third-party/GPL-3.0.txt) |
| [NumPy](https://github.com/numpy/numpy) | CAD workers | BSD-3-Clause; binary distributions may have additional notices | [License](licenses/third-party/NumPy-LICENSE.txt) |
| [SciPy](https://github.com/scipy/scipy) | STEP worker | BSD-3-Clause; binary distributions may have additional notices | [License](licenses/third-party/SciPy-LICENSE.txt) |
| [trimesh](https://github.com/mikedh/trimesh) | CAD workers | MIT | [License](licenses/third-party/trimesh-LICENSE.txt) |
| [Pillow](https://github.com/python-pillow/Pillow) | Server and FreeCAD workers | MIT-CMU; bundled codecs have separate terms | [License](licenses/third-party/Pillow-LICENSE.txt) |
| [PostgreSQL](https://www.postgresql.org/) | Database service | PostgreSQL License | [Copyright and license](licenses/third-party/PostgreSQL-COPYRIGHT.txt) |
| [pgvector](https://github.com/pgvector/pgvector) | Database extension | PostgreSQL License | [License](licenses/third-party/pgvector-LICENSE.txt) |
| [Miniforge](https://github.com/conda-forge/miniforge) | CAD worker base images | Installer code: BSD-3-Clause; installed packages have independent terms | [License](licenses/third-party/Miniforge-LICENSE.txt) |
| [.NET runtime](https://github.com/dotnet/runtime) | Self-contained Local CAD Agent packages | MIT and additional third-party terms | [License](licenses/third-party/dotnet-runtime-LICENSE.txt), [third-party notices](licenses/third-party/dotnet-runtime-THIRD-PARTY-NOTICES.txt) |

These entries identify important declared and native dependencies. They are not
an exhaustive inventory of Python packages, Conda packages, OS packages, FreeCAD
modules, or libraries bundled in wheels. In particular, check Qt/PySide, Coin,
Boost, OpenSSL, libpq, BLAS/LAPACK, image codecs, and compiler runtimes when they
are present in a release artifact. Their applicable terms must come from the
actual packages shipped.

## Frontend runtime dependencies

The following versions and MIT license files were obtained from the npm archives
recorded in `front-end/package-lock.json`. Development-only tools are outside this
runtime list. Regenerate this list and its notices when the lockfile changes.

| Package | Locked version | Copyright and license |
|---|---|---|
| `@tanstack/react-table` | 8.21.3 | [MIT notice](licenses/third-party/npm-tanstack-react-table-LICENSE.txt) |
| `@tanstack/table-core` | 8.21.3 | [MIT notice](licenses/third-party/npm-tanstack-table-core-LICENSE.txt) |
| `cookie` | 1.1.1 | [MIT notice](licenses/third-party/npm-cookie-LICENSE.txt) |
| `fflate` | 0.8.3 | [MIT notice](licenses/third-party/npm-fflate-LICENSE.txt) |
| `js-tokens` | 4.0.0 | [MIT notice](licenses/third-party/npm-js-tokens-LICENSE.txt) |
| `loose-envify` | 1.4.0 | [MIT notice](licenses/third-party/npm-loose-envify-LICENSE.txt) |
| `react` | 18.3.1 | [MIT notice](licenses/third-party/npm-react-LICENSE.txt) |
| `react-dom` | 18.3.1 | [MIT notice](licenses/third-party/npm-react-dom-LICENSE.txt) |
| `react-router` | 7.18.5 | [MIT notice](licenses/third-party/npm-react-router-LICENSE.txt) |
| `react-router-dom` | 7.18.5 | [MIT notice](licenses/third-party/npm-react-router-dom-LICENSE.txt) |
| `scheduler` | 0.23.2 | [MIT notice](licenses/third-party/npm-scheduler-LICENSE.txt) |
| `set-cookie-parser` | 2.7.3 | [MIT notice](licenses/third-party/npm-set-cookie-parser-LICENSE.txt) |
| `three` | 0.160.1 | [MIT notice](licenses/third-party/npm-three-LICENSE.txt) |

## Bundled Draco decoder assets

The frontend imports Draco JavaScript and WebAssembly decoders from
`three/examples/jsm/libs/draco/gltf/`. These assets are maintained by
[Google Draco](https://github.com/google/draco) and have separate Apache-2.0 terms;
the Three.js MIT notice does not replace them. See the [Draco license](licenses/third-party/Draco-LICENSE.txt)
and [authors](licenses/third-party/Draco-AUTHORS.txt). The included Draco 1.5.6
texts are upstream reference copies; establish the exact decoder source revision
and any additional applicable notices before redistributing a binary release.

The frontend build emits `third-party-licenses.txt` with Modelryx's license,
locked npm runtime notices, and the Draco reference notices. Its license links
are available on the login screen and application header. This improves notice
delivery; it does not certify that all native or bundled components have been
inventoried.

## Scope and provenance

The native/runtime license files are reference copies from identified upstream
releases. They do not establish which versions a Docker build will install.
FreeCAD is selected by a build argument; STEP and other Python/Conda dependencies
have version ranges, and some base images use mutable tags. Verify the actual
resolved packages for every published image or executable.

[License sources](licenses/third-party/sources.json) records each download URL,
reference release or npm package version where available, archive member, and
SHA-256 of the saved text. Preserve upstream texts verbatim. Copyright ownership
is recorded in the supplied notices and source packages; these acknowledgments
do not imply endorsement.

For redistribution instructions and remaining release verification, see
[third-party licensing](docs/operations/third-party-licensing.md).
