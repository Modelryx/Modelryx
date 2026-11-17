# Third-party licensing and release packaging

Modelryx's own source code uses Apache-2.0. Dependencies retain their separate
licenses. Start with the [third-party notices](../../THIRD_PARTY_NOTICES.md) and
[license texts](../../licenses/third-party/README.md).

## Publishing source

Keep the Modelryx `LICENSE`, third-party notices, and any upstream copyright and
license headers in copied or modified third-party source. Clearly identify
third-party modifications when required. A dependency declaration or Dockerfile
that downloads a library is different from distributing a copy of its binaries.
Do not describe all dependencies as Apache-2.0.

## Publishing images and executables

Before publishing each artifact:

1. Record the source revision, image digest, resolved package versions/builds,
   and included native libraries. Review all runtime and bundled dependencies,
   including packages in the base image. Package metadata is an inventory aid,
   not a substitute for reading the supplied license files.
2. Collect the copyright notices, complete license texts, exceptions and other
   required notices from those exact packages. Replace reference copies when
   their text differs from the shipped release. Retain additional per-file and
   bundled-library terms.
3. For LGPL-covered binaries, provide corresponding library source and build
   material, including applicable patches, through a method permitted by the
   relevant license. For a release, hosting the matching source alongside the
   binaries is preferable to relying on a generic upstream homepage. Verify
   users can exercise the applicable modification/replacement/relinking rights;
   do not impose terms prohibiting reverse engineering needed to debug such
   modifications. LGPL-3.0 incorporates GPL-3.0, so include both texts.
4. Include Modelryx's license and the applicable third-party materials in the
   delivered artifact. Record how recipients can obtain the corresponding source.
   Do not publish an unimplemented source offer or claim source availability
   before the referenced files are actually available.

Docker images are distributed binaries when shared with users. Include applicable
notices, for example under `/usr/share/licenses/modelryx/`, and document the path
in the image's release notes. Preserve upstream notices already supplied in the
image; `conda clean --all` removes package caches, so do not rely on cache-only
license files remaining available.

A frontend build distributes JavaScript to browsers. Include a consolidated
third-party license file in the deployed `dist` output containing the actual
copyright and license notices for bundled dependencies. An About or Open-source
licenses link is a useful access point. Preserve required notices when minifying.

Self-contained Local CAD Agent packages include .NET runtime components. Deliver
its matching runtime license and third-party notices alongside the executable,
plus notices for any bundled preprocessors or native libraries. A separately
installed FreeCAD runtime is distinct from a FreeCAD runtime shipped in an
installer; describe that boundary accurately.

## Current documentation coverage

The repository provides an upstream license reference collection and notices for
the frontend runtime packages in its lockfile. The frontend Vite build emits `third-party-licenses.txt` and exposes license links
on the login screen and application header. It checks locked npm package versions
and the saved license-file hashes. Review bundled decoder provenance separately.
Docker images and agent installer packages still need a complete, verified
third-party notice/source distribution process.

Before a binary release, complete the artifact-specific inventory, source
availability and packaging steps above. Do not treat this reference collection
as certification that a particular image or executable meets every dependency's
redistribution requirements.

## Upstream terms

- [FreeCAD 1.1.3 license](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/LICENSE)
- [Open CASCADE license and exception](https://dev.opencascade.org/doc/overview/html/occt_public_license.html)
- [pythonocc-core license](https://github.com/tpaviot/pythonocc-core/blob/master/LICENSE)
- [Psycopg license](https://github.com/psycopg/psycopg/blob/master/LICENSE.txt)

This guide describes release engineering requirements; unusual linking,
modifications, or proprietary redistribution terms may require legal review.
