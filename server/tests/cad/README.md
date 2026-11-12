# PartMetadata regression corpus

`manifest.json` defines deterministic FreeCAD fixtures and their expected normalized engineering
metadata. Run `generate_and_verify_freecad.py` inside the FreeCAD worker environment; it creates
temporary FCStd documents, runs the production converter/normalizer, and compares the resulting
PartMetadata with the checked-in expectations.

The corpus intentionally stores expected engineering facts rather than generated FCStd binaries.
This keeps fixtures reproducible across platforms and avoids opaque binary test files. Add a case by
adding its builder to the script and its expected subset to `expected/`.

Cross-format tests use `modelryx.metadata.compare_part_metadata` with Phase 0 tolerances:

- bounding-box dimensions: ±0.01 mm
- volume and surface area: ±0.1%
- solid and face counts: exact when both formats provide them

Feature history is deliberately excluded from cross-format equivalence.

CI also builds the production FreeCAD worker image and runs two native-runtime release gates. The
golden gate verifies the FCStd-to-PFG conversion contract. The dependency gate creates a nested
assembly with repeated links to an external component, packages it, reopens it in isolation, and
checks hierarchy, placements, source color, rendered instances, bounds, unused-file exclusion, and nested
missing-reference reporting.
