# Modelryx PartMetadata v1 Specification

The wire version is `1.0`. The authoritative, executable field contract is
[`part_metadata_v1.json`](../../server/src/server/modelryx/metadata/schemas/part_metadata_v1.json).
This document explains the schema's engineering meaning, normalization rules, and responsibility
boundaries. If an example or prose field list conflicts with the JSON schema, the JSON schema wins.
Worker support for optional fields varies and must be represented by capabilities and quality data.

## 1. Purpose

`PartMetadata` is the CAD-neutral metadata format used by Modelryx to describe a part or assembly independently of its source CAD system.

Its primary goals are to support:

- duplicate and similarity detection
- AI-assisted search
- structured filtering
- revision comparison
- validation
- engineering analytics
- downstream engineering search and automation

The worker is responsible for converting CAD-specific information into `PartMetadata`.

```text
FreeCAD ─────┐
External CAD ┼──> CAD Worker ──> PartMetadata v1
STEP ────────┘
```

Consumers of `PartMetadata` must not need to understand the originating CAD system.

---

## 2. Design Principles

### 2.1 CAD Neutral

No required field may depend on any CAD-specific concept.

Avoid exposing:

```text
PartDesign::Pad
VendorAPI.Features.Extrude
```

as standard feature types.

Normalize them to engineering concepts such as:

```text
extrusion
```

### 2.2 Engineering-Oriented

Metadata should describe the engineering object rather than the CAD application's internal object model.

Prefer:

```json
{
  "hole_count": 4
}
```

instead of CAD-specific object lists.

### 2.3 Stable and Versioned

Every metadata document must contain:

```json
{
  "schema": "modelryx.part-metadata",
  "schema_version": "1.0"
}
```

### 2.4 Search-Friendly

Fields commonly used for filtering or similarity should use normalized units.

Canonical v1 units:

```text
length        mm
area          mm²
volume        mm³
mass          kg
density       kg/m³
angle         degrees
```

### 2.5 Extensible

CAD-specific information may be returned under an optional `native` section.

AI/search services should normally ignore `native`.

---

## 3. Top-Level Structure

Recommended serialized form:

```json
{
  "schema": "modelryx.part-metadata",
  "schema_version": "1.0",

  "source": {},
  "identity": {},
  "geometry": {},
  "physical": {},
  "features": {},
  "drawings": [],
  "assembly": {},
  "properties": {},
  "quality": {},

  "native": {}
}
```

This is a section inventory, not a valid document with empty required objects.

Required sections in v1:

```text
schema
schema_version
source
identity
geometry
quality
```

Optional sections:

```text
physical
features
drawings
assembly
properties
native
```

Standard objects reject unknown fields. Use `properties` for supported generic values
and `native` for CAD-specific extensions. Optional means the field may be omitted;
it does not imply that `null` is accepted. Null is accepted only where explicitly
declared by the JSON Schema.

---

## 4. Source

The `source` section records where the metadata came from.

It is informational and should normally not affect semantic search.

```json
{
  "source": {
    "system": "freecad",
    "system_version": "1.0.2",
    "worker": "modelryx-freecad-worker",
    "worker_version": "0.3.0",
    "extracted_at": "2026-08-20T22:30:00Z"
  }
}
```

| Field | Type | Required | Description |
|---|---|---:|---|
| `system` | string | yes | Source CAD/data system |
| `system_version` | string/null | no | Source application version |
| `worker` | string | yes | Worker implementation |
| `worker_version` | string | yes | Worker version |
| `extracted_at` | ISO-8601 datetime | yes | Extraction timestamp |

Recommended `system` values:

```text
freecad
external_cad
step
other
```

This identifies the source only. Consumers must not depend on it to interpret common metadata.

---

## 5. Identity

The `identity` section describes the engineering object.

```json
{
  "identity": {
    "name": "MotorHousing",
    "display_name": "Motor Housing",
    "description": "Main housing for servo motor",
    "kind": "part",
    "part_number": "MH-1002",
    "revision": "A",
    "classification": [
      "housing",
      "machined"
    ],
    "tags": [
      "motor",
      "aluminum"
    ]
  }
}
```

| Field | Type | Required |
|---|---|---:|
| `name` | string | yes |
| `display_name` | string/null | no |
| `description` | string/null | no |
| `kind` | enum | yes |
| `part_number` | string/null | no |
| `revision` | string/null | no |
| `classification` | string[] | no |
| `tags` | string[] | no |

`kind` values in v1:

```text
part
assembly
unknown
```

The authoritative Modelryx Item ID should normally remain in the PDM database rather than be embedded by the CAD worker.

At ingestion, the managed Item type is authoritative: `ASSEMBLY` maps to
`identity.kind = "assembly"`; `PART` maps to `"part"`. The server also sets
`assembly.is_assembly`. For a managed part it removes component counts and sets
`quality.assembly = "not_applicable"`. This canonicalization precedes validation;
the raw worker artifact remains available for provenance. CAD-derived names,
part numbers, and revision labels are descriptive source values, not database IDs
or authoritative working-version selectors.

---

## 6. Geometry

`geometry` is the most important v1 section for similarity search.

```json
{
  "geometry": {
    "bounding_box": {
      "x_mm": 120.0,
      "y_mm": 85.0,
      "z_mm": 45.0
    },

    "center": {
      "x_mm": 0.0,
      "y_mm": 0.0,
      "z_mm": 22.5
    },

    "topology": {
      "solid_count": 1,
      "shell_count": 1,
      "face_count": 74,
      "edge_count": 156,
      "vertex_count": 84
    },

    "shape": {
      "volume_mm3": 400000.0,
      "surface_area_mm2": 148000.0
    }
  }
}
```

The example volume is intentionally less than the bounding-box volume (`120 × 85 × 45 = 459000 mm³`). Engineering validation rejects a part volume larger than its enclosing bounding-box volume, allowing numerical tolerance. Assembly volume is summed over instances and may exceed the enclosing box when instances overlap.

`geometry.bounding_box` and `geometry.shape` are required, including all three
dimensions and both shape values. `center` and `topology` are optional. A supplied
center requires all three coordinates; each topology count is independently optional.

---

## 7. Bounding Box

Canonical representation:

```json
{
  "bounding_box": {
    "x_mm": 120.0,
    "y_mm": 85.0,
    "z_mm": 45.0
  }
}
```

Workers must convert source units to millimetres.

### Orientation Rule

For v1, preserve dimensions relative to the source/model coordinate system.

Do not automatically sort dimensions from largest to smallest in `bounding_box`.

For search purposes the server may derive:

```text
bbox_min
bbox_mid
bbox_max
bbox_sorted
```

independently.

This preserves actual geometry metadata while allowing orientation-independent similarity calculations.

---

## 8. Shape Properties

```json
{
  "shape": {
    "volume_mm3": 400000.0,
    "surface_area_mm2": 148000.0
  }
}
```

For multiple disconnected solids:

```text
volume = sum of valid solid volumes
surface_area = total calculated surface area of valid solids
```

---

## 9. Topology

```json
{
  "topology": {
    "solid_count": 1,
    "shell_count": 1,
    "face_count": 74,
    "edge_count": 156,
    "vertex_count": 84
  }
}
```

These fields are CAD-neutral and can provide inexpensive similarity signals.

Metadata should describe the effective final engineering shape. Construction geometry, datum objects, sketches, and suppressed bodies should normally be excluded.

---

## 10. Physical Properties

```json
{
  "physical": {
    "material": {
      "name": "Aluminum 6061",
      "normalized_name": "aluminum_6061"
    },

    "density_kg_m3": 2700.0,
    "mass_kg": 1.08,

    "center_of_mass": {
      "x_mm": 2.4,
      "y_mm": -1.2,
      "z_mm": 15.6
    }
  }
}
```

Recommended fields:

```text
material
density_kg_m3
mass_kg
center_of_mass
```

All are optional.

A worker must not calculate mass from an arbitrary default density unless explicitly configured to do so.

If material, density, mass, or center of mass is unknown, omit the field rather than
inventing a value. These physical fields do not accept `null`. A supplied material
requires a nonempty `name`; `normalized_name` is optional and nonempty when supplied.

---

## 11. Material

Use both human-readable and normalized names where possible.

```json
{
  "material": {
    "name": "Aluminum 6061-T6",
    "normalized_name": "aluminum_6061_t6"
  }
}
```

`name` represents the CAD/source value.

`normalized_name` represents Modelryx normalization.

Examples such as:

```text
Aluminium 6061
AL6061
6061-T6 Aluminum
Al 6061 T6
```

may eventually normalize to:

```text
aluminum_6061_t6
```

For v1, normalization may be performed by either the worker or server. Long term, the Modelryx server should own material normalization.

---

## 12. Feature Summary

Feature metadata should describe recognized engineering features, not CAD history object types.

```json
{
  "features": {
    "source": "history",

    "summary": {
      "hole_count": 8,
      "fillet_count": 12,
      "chamfer_count": 4,
      "extrusion_count": 3,
      "revolution_count": 0,
      "pocket_count": 2,
      "pattern_count": 1,
      "thread_count": 0
    }
  }
}
```

Supported v1 feature classes:

```text
hole
fillet
chamfer
extrusion
revolution
pocket
pattern
thread
```

Recommended feature source values:

```text
history
geometry_inferred
mixed
```

Workers may return zero only when they can reliably determine that the feature does not exist.

If a worker cannot determine a feature type, omit it or report the limitation through quality metadata.

---

## 13. Detailed Hole Information

Holes are sufficiently important for part similarity that v1 should optionally support richer information.

```json
{
  "features": {
    "source": "history",

    "summary": {
      "hole_count": 8
    },

    "holes": [
      {
        "diameter_mm": 8.0,
        "depth_mm": null,
        "through": true,
        "count": 4
      },
      {
        "diameter_mm": 5.0,
        "depth_mm": 12.0,
        "through": false,
        "count": 4
      }
    ]
  }
}
```

This enables searches such as:

> Find parts with four 8 mm through holes.

Detailed hole extraction is optional for the first implementation. `summary.hole_count` is sufficient for the initial milestone.

For geometry-inferred holes, future versions may optionally include recognition confidence.

### 13.1 Normalized Feature Records

Revision comparison and CAD inspection also accept optional `features.items`.
When `features` is present, `source` and `summary` are required; an empty summary
is valid when counts are unavailable.

```json
{
  "features": {
    "source": "history",
    "summary": {"extrusion_count": 1},
    "items": [
      {
        "persistent_id": "Pad",
        "name": "Base extrusion",
        "type": "EXTRUSION",
        "properties": {"length_mm": 20.0}
      }
    ]
  }
}
```

Each record requires `type` and `properties` (the scalar/array vocabulary in section
15). Optional `persistent_id` and `name` accept string/null, with a 255-character
limit. Stable IDs support matching and CAD operation targeting; the schema does
not enforce uniqueness or persistence across files. CAD inspection requires a
nonempty persistent ID for an addressable feature.

The exact item-type vocabulary is:

```text
HOLE POCKET PAD EXTRUSION FILLET CHAMFER BOSS SLOT PATTERN SHELL
REVOLVE THREAD BODY SKETCH UNKNOWN_FEATURE
```

These uppercase item types are separate from the lowercase summary vocabulary.
`BODY` and `SKETCH` support inspection; they do not change the rule that aggregate
geometry describes the effective final shape. Item records are not a full feature
tree or sketch-constraint graph. The FreeCAD converter currently uses source
object identity for `persistent_id`; consumers must not assume a global UUID.

### 13.2 Normalized Drawings

The optional top-level `drawings` array supports file, text, and structured drawing
comparison. Each record requires nonempty `stable_id`, `filename` (both at most
255 characters), and `worker_contract_version`.

| Field | Contract |
|---|---|
| `artifact_id`, `mime_type`, `sha256` | Optional string/null |
| `byte_size` | Optional non-negative integer |
| `page_count` | Optional integer, minimum 1 |
| `sections` | Optional array of stable text sections |
| `entities` | Optional array of normalized drawing entities |

Sections require `stable_id`, `kind`, `page`, and `text`. Kinds are `SECTION`,
`NOTES`, `TITLE_BLOCK`, `REVISION_TABLE`, `TABLE`, and `OTHER`.

Entities require `stable_id`, `type`, and `page`. Types are `DIMENSION`,
`TOLERANCE`, `ANNOTATION`, `VIEW`, `GDT`, `WELD`, `FINISH`, and `UNKNOWN_ENTITY`.
Optional `value` and `tolerance` use section 15's value types; `unit`, `text`, and
`view` accept string/null; `properties` uses section 15's property map.

Section/entity IDs are nonempty strings of at most 255 characters, and pages are
1-based integers. Both records may include `bbox`, an array of exactly four numbers.
The schema does not define bbox coordinate units/origin, so these must be agreed
through the drawing worker contract. There is no `quality.drawings` field in v1.

```json
{
  "drawings": [{
    "stable_id": "drawing-main",
    "filename": "housing.pdf",
    "worker_contract_version": "1.0",
    "page_count": 1,
    "sections": [{
      "stable_id": "notes-1", "kind": "NOTES", "page": 1,
      "text": "Remove sharp edges."
    }],
    "entities": [{
      "stable_id": "diameter-1", "type": "DIMENSION", "page": 1,
      "value": 8.0, "unit": "mm"
    }]
  }]
}
```

This is a supported transport contract, not a guarantee of extraction capability.
The current FreeCAD normalizer does not emit `drawings`. A PDF preview or changed
file hash alone does not establish a dimension/tolerance change; structured
comparison requires worker-supplied normalized entities. Visual page diff and
general GD&T recognition are not implied by these fields.

---

## 14. Assembly

For a normal part:

```json
{
  "assembly": {
    "is_assembly": false
  }
}
```

For an assembly:

```json
{
  "assembly": {
    "is_assembly": true,
    "instance_count": 37,
    "unique_component_count": 14,
    "subassembly_count": 3
  }
}
```

Recommended v1 fields:

| Field | Meaning |
|---|---|
| `is_assembly` | Whether the source represents an assembly |
| `instance_count` | Total component instances |
| `unique_component_count` | Number of unique components |
| `subassembly_count` | Number of subassemblies |

Do not make `PartMetadata` the authoritative BOM.

```text
PartMetadata
    → searchable assembly characteristics

Modelryx BOM
    → authoritative component relationships
```

This prevents duplication and synchronization problems.

---

## 15. Generic Engineering Properties

Workers may encounter useful properties that do not yet have standardized Modelryx fields.

Use:

```json
{
  "properties": {
    "manufacturer": "ABC Motors",
    "finish": "Anodized",
    "drawing_number": "DWG-10025"
  }
}
```

Restrictions for v1:

- values should be simple scalar values where possible
- do not place large CAD object structures here
- do not use this section as a dump of all CAD properties
- frequently used properties should eventually become formal standard fields

Supported value types:

```text
string
number
boolean
null
string[]
number[]
```

---

## 16. Native Metadata

CAD-specific information may optionally be included:

```json
{
  "native": {
    "body_name": "Body",
    "tip": "Fillet003",
    "object_count": 18,
    "document_label": "MotorHousing"
  }
}
```

This section is explicitly outside the common standard.

Its interpretation is controlled by `source.system`.

Modelryx AI search should not normally generate embeddings directly from `native`.

Native metadata exists primarily for:

- debugging
- CAD-specific functionality
- worker diagnostics
- future metadata migration
- advanced CAD-specific tools

---

## 17. Quality Section

Metadata quality is important because STEP, native CAD, and other formats provide different amounts of information.

```json
{
  "quality": {
    "geometry": "complete",
    "physical": "partial",
    "features": "partial",
    "assembly": "not_applicable",

    "warnings": [
      "Material was not available from source.",
      "Feature history unavailable; features inferred from geometry."
    ]
  }
}
```

All four domain states (`geometry`, `physical`, `features`, `assembly`) and
`warnings` are required, even when an optional data section is absent. An empty
warnings array is valid.

Allowed states:

```text
complete
partial
unavailable
unsupported
not_applicable
```

### `complete`

Worker successfully extracted the expected information.

### `partial`

Some information could be extracted.

### `unavailable`

Worker supports extraction, but the source data does not contain it.

Example:

```text
FreeCAD supports material extraction,
but material was never assigned.
```

### `unsupported`

Worker currently cannot extract that information.

Example:

```text
STEP worker does not yet support feature recognition.
```

### `not_applicable`

Information does not apply.

For AI/search purposes:

```text
unavailable != unsupported != zero
```

---

## 18. Example — FreeCAD Part

```json
{
  "schema": "modelryx.part-metadata",
  "schema_version": "1.0",

  "source": {
    "system": "freecad",
    "system_version": "1.0.2",
    "worker": "modelryx-freecad-worker",
    "worker_version": "0.3.0",
    "extracted_at": "2026-08-20T22:30:00Z"
  },

  "identity": {
    "name": "MotorHousing",
    "display_name": "Motor Housing",
    "description": "Servo motor aluminum housing",
    "kind": "part",
    "part_number": "MH-1002",
    "revision": "A",
    "classification": [
      "housing",
      "machined"
    ],
    "tags": [
      "motor",
      "aluminum"
    ]
  },

  "geometry": {
    "bounding_box": {
      "x_mm": 120.0,
      "y_mm": 85.0,
      "z_mm": 45.0
    },

    "center": {
      "x_mm": 0.0,
      "y_mm": 0.0,
      "z_mm": 22.5
    },

    "shape": {
      "volume_mm3": 400000.0,
      "surface_area_mm2": 148000.0
    },

    "topology": {
      "solid_count": 1,
      "shell_count": 1,
      "face_count": 74,
      "edge_count": 156,
      "vertex_count": 84
    }
  },

  "physical": {
    "material": {
      "name": "Aluminum 6061-T6",
      "normalized_name": "aluminum_6061_t6"
    },

    "density_kg_m3": 2700.0,
    "mass_kg": 1.08,

    "center_of_mass": {
      "x_mm": 2.4,
      "y_mm": -1.2,
      "z_mm": 15.6
    }
  },

  "features": {
    "source": "history",

    "summary": {
      "hole_count": 8,
      "fillet_count": 12,
      "chamfer_count": 4,
      "extrusion_count": 3,
      "pocket_count": 2,
      "pattern_count": 1
    },

    "holes": [
      {
        "diameter_mm": 8.0,
        "depth_mm": null,
        "through": true,
        "count": 4
      },
      {
        "diameter_mm": 5.0,
        "depth_mm": 12.0,
        "through": false,
        "count": 4
      }
    ]
  },

  "assembly": {
    "is_assembly": false
  },

  "properties": {
    "finish": "Anodized"
  },

  "quality": {
    "geometry": "complete",
    "physical": "complete",
    "features": "complete",
    "assembly": "not_applicable",
    "warnings": []
  },

  "native": {
    "body_name": "Body",
    "tip": "Fillet003",
    "object_count": 18
  }
}
```

---

## 19. Example — STEP Part

The same engineering part represented by STEP may provide less information:

```json
{
  "schema": "modelryx.part-metadata",
  "schema_version": "1.0",

  "source": {
    "system": "step",
    "system_version": null,
    "worker": "modelryx-step-worker",
    "worker_version": "0.1.0",
    "extracted_at": "2026-08-20T22:31:00Z"
  },

  "identity": {
    "name": "MotorHousing",
    "kind": "part"
  },

  "geometry": {
    "bounding_box": {
      "x_mm": 120.0,
      "y_mm": 85.0,
      "z_mm": 45.0
    },

    "shape": {
      "volume_mm3": 400002.3,
      "surface_area_mm2": 148001.8
    },

    "topology": {
      "solid_count": 1,
      "shell_count": 1,
      "face_count": 74,
      "edge_count": 156,
      "vertex_count": 84
    }
  },

  "assembly": {
    "is_assembly": false
  },

  "quality": {
    "geometry": "complete",
    "physical": "unavailable",
    "features": "unsupported",
    "assembly": "not_applicable",

    "warnings": [
      "Material is unavailable from source.",
      "Parametric feature history is unavailable."
    ]
  }
}
```

From the AI/search perspective, both documents expose the same geometry vocabulary.

---

## 20. Worker Contract

A worker should conceptually expose:

```text
extract_metadata(source)
    -> PartMetadata
```

FreeCAD:

```text
FCStd
  ↓
FreeCAD API
  ↓
FreeCADMetadataExtractor
  ↓
PartMetadataNormalizer
  ↓
PartMetadata v1
```

STEP:

```text
STEP
 ↓
OpenCascade
 ↓
STEPMetadataExtractor
 ↓
PartMetadataNormalizer
 ↓
PartMetadata v1
```

Everything after `PartMetadataNormalizer` can be shared.

---

## 21. Responsibility Boundary

### CAD Worker Owns

```text
Opening native CAD data

Extracting:
    geometry
    topology
    source material
    mass properties
    available features
    assembly statistics

Unit conversion

Mapping CAD concepts to Modelryx concepts

Producing PartMetadata-compatible worker metadata
```

### Modelryx Server Owns

```text
Item ID
Revision lifecycle
Permissions
BOM authority
File relationships
Metadata persistence
Schema validation
Engineering validation
Metadata migration
Material normalization
Derived geometry fields
```

### AI/Search Service Owns

```text
Search-document creation
Text normalization
Feature weighting
Numerical similarity
Embeddings
Vector indexing
Query interpretation
Ranking
AI reasoning
```

The worker should not generate embeddings.

---

## 22. PartSearchDocument Is a Separate Model

`PartMetadata` must not become tied to a particular AI implementation.

`PartSearchDocument` below is an architectural illustration, not the persisted
PartMetadata wire format or an API response schema. The server already maintains
derived/indexed metadata and separate AI documents and knowledge sources.

Use:

```text
PartMetadata
      ↓
SearchDocumentBuilder
      ↓
PartSearchDocument
```

Example:

```json
{
  "item_id": "PF-00124",
  "revision": "A",

  "filters": {
    "kind": "part",
    "material": "aluminum_6061_t6",
    "bbox_x_mm": 120,
    "bbox_y_mm": 85,
    "bbox_z_mm": 45,
    "mass_kg": 1.08
  },

  "geometry_signature": {
    "bbox_sorted_mm": [45, 85, 120],
    "volume_mm3": 400000,
    "surface_area_mm2": 148000,
    "face_count": 74,
    "edge_count": 156
  },

  "semantic_text": "Motor Housing. Aluminum 6061-T6 machined housing. Dimensions approximately 120 by 85 by 45 millimeters. Eight holes, twelve fillets, four chamfers.",

  "embedding": []
}
```

The embedding is derived search data and must not be generated or owned by the CAD worker.

---

## 23. Initial Similarity Fields

For the first Modelryx AI-search implementation, use these fields:

```text
Identity
    name
    description
    classification
    tags

Geometry
    bounding box
    volume
    surface area
    solid count
    face count
    edge count

Physical
    material
    mass

Features
    hole count
    fillet count
    chamfer count
    extrusion count
    pocket count

Assembly
    part/assembly
    instance count
    unique component count
```

These provide enough information for useful similarity search without requiring full geometric AI.

---

## 24. Similarity-consumer boundary

```text
                      New Part
                         │
                         ▼
                      SHA-256
                         │
              ┌──────────┴──────────┐
              │                     │
          identical              different
              │                     │
              ▼                     ▼
            done             PartMetadata v1
                                    │
                                    ▼
                         Structured filtering
                                    │
                            candidate set
                                    │
                                    ▼
                         Numeric similarity
                                    │
                              top candidates
                                    │
                                    ▼
                            Vector search
                                    │
                              top candidates
                                    │
                                    ▼
                         Optional LLM analysis
```

AI therefore does not need to understand:

```text
.FCStd
.PRT
.SLDPRT
.CATPart
.STEP
```

It understands Modelryx engineering/search data derived from `PartMetadata`.

---

## 25. V1 field tiers

### Required for v1

```text
schema/version
source
name
kind

bounding box
volume
surface area

quality/status information
```

### Strongly Recommended

```text
solid count
face count
edge count
material
mass
center of mass
feature counts
assembly summary
description
classification
tags
```

### Optional v1

```text
detailed holes
normalized feature items
normalized drawing sections and entities
custom properties
native metadata
```

Fields not present in the executable v1 schema are not valid v1 wire fields. Candidate additions,
including richer feature trees, PMI, tolerances, constraints, fingerprints, and embeddings, are
For the complete PartMetadata specification and evolution, see [PartMetadata v1](partmetadata-v1.md).

---

## 26. Storage and Revision Rule

Metadata describing geometry belongs to an `ItemRevision`, not the base Item.

```text
Item PF-00125

Revision A
    PartMetadata A

Revision B
    PartMetadata B

Revision C
    PartMetadata C
```

The implemented persistence relationship is:

```text
ItemRevision
    │
    ├── RevisionContentSnapshot (sequence 1, 2, ... within the revision)
    │      └── immutable membership of related Artifacts
    ├── current_metadata_extraction_id
    └── MetadataExtraction (immutable extraction history)
           ├── snapshot_id
           ├── raw_artifact_id
           ├── schema_version
           ├── worker_type
           ├── worker_version
           ├── extracted_at
           ├── status
           ├── metadata_json
           └── derived_json
```

The complete normalized document and deterministic derived fields are stored as
PostgreSQL `JSONB`, with frequently queried values also persisted in indexed/typed
columns. Raw worker metadata is retained as a separate artifact. Normal ingestion
appends an extraction and updates the revision's current extraction pointer; it
does not overwrite an earlier extraction.

Geometry metadata therefore belongs to a specific revision **and immutable content snapshot**.
CAD-session commits can create a new working version in the current revision or
a new revision with its first working version. Each result ingests its own metadata
and records the version/artifact provenance. Checkout ownership, lifecycle state,
version numbering, and commit mode belong to server workflow models, not new fields
inside PartMetadata. Historical consumers must use the extraction/version they
intend to inspect rather than assume the revision's current pointer is historical.

Extraction status is server-derived after validation:

- `INVALID`: `quality.geometry` is not `complete`.
- `READY`: geometry is complete and physical/features (plus assembly for an
  assembly Item) are each `complete` or `not_applicable`.
- `PARTIAL`: geometry is complete but another relevant domain is incomplete.

Schema or engineering violations raise an ingestion error; `INVALID` here is the
status of a structurally accepted document with incomplete geometry, not a promise
that rejected documents are persisted as extraction rows.

Developer inspection endpoints are:

```text
GET /api/v1/revisions/{revision_id}/metadata
GET /api/v1/revisions/{revision_id}/metadata/extractions
GET /api/v1/revisions/{revision_id}/metadata/extractions/{extraction_id}/raw
```

Item attachment versions are separate records. Uploading or previewing an attachment
does not automatically create normalized drawing entities or a geometry extraction.

---

## 27. Derived Geometry

The worker should preserve model-coordinate dimensions:

```text
x = 120
y = 85
z = 45
```

The server may derive orientation-independent values:

```text
bbox_sorted = [45, 85, 120]

bbox_min = 45
bbox_mid = 85
bbox_max = 120
```

Other useful derived values:

```text
bounding_box_volume =
    bbox_x × bbox_y × bbox_z

fill_ratio =
    volume / bounding_box_volume

aspect ratios
```

These are search/index data and do not need to be returned by every CAD worker.

Current `derived_json` keys are `bbox_sorted_mm`, `bbox_min_mm`, `bbox_mid_mm`,
`bbox_max_mm`, `bounding_box_volume_mm3`, `fill_ratio`, `aspect_xy`, `aspect_yz`,
and `aspect_xz`, plus `material_search` when material is available. Ratios with a
zero denominator are `null`. Material search currently trims and case-folds the
normalized name or source name; it does not resolve alloy synonyms. Assembly
`fill_ratio` may exceed 1 when instances overlap.

---

## 28. Validation Requirements

Two levels of validation should be used.

### Schema Validation

Examples:

```text
schema/version present
numeric fields have correct types
enum values are recognized
dimensions are non-negative
```

### Engineering Validation

Examples:

```text
volume cannot be negative

mass cannot be negative

part volume cannot exceed bounding-box volume (with numerical tolerance)

solid_count = 0 conflicts with positive solid volume

part/assembly fields must be logically consistent
```

Optional missing information should not invalidate otherwise usable metadata.

The implemented part-volume tolerance is `max(1e-6, bounding_box_volume * 1e-9)`
in mm³. Assembly instance sums are exempt from this upper-bound check. Positive
solid counts require three positive box dimensions. Non-assemblies cannot report
positive instance counts, and `unique_component_count` cannot exceed
`instance_count` (which defaults to zero for this check).

These checks do not yet enforce every plausible engineering relationship, such as
density/mass consistency or detailed-hole totals matching summary counts.

---

## 29. Cross-Format Equivalence Requirement

The key acceptance test for `PartMetadata v1` is that the same engineering geometry represented in different CAD formats produces approximately equivalent normalized metadata.

```text
             Same engineering part

        ┌───────────┴───────────┐
        ▼                       ▼
    FreeCAD FCStd              STEP
        │                       │
        ▼                       ▼
 FreeCAD Worker          STEP processing
        │                       │
        ▼                       ▼
 PartMetadata v1         PartMetadata v1
        │                       │
        └───────────┬───────────┘
                    ▼
             Compare metadata
```

Implemented default numerical tolerances:

```text
bounding box: ±0.01 mm
volume:       ±0.1%
surface area: ±0.1%
```

Feature-history differences are acceptable when correctly represented through quality/source information.

The equivalence helper additionally compares `solid_count` and `face_count`
exactly when both documents supply them. It compares box axes in model coordinates,
not sorted dimensions, and uses a `1e-9` absolute floor for shape-value comparisons.

---

## 30. Final Architecture Rule

```text
CAD system
      │
      ▼
    Worker
      │
      ▼
PartMetadata v1
      │
      ├──────────> Validation
      │
      ├──────────> Duplicate Detection
      │
      ├──────────> Revision Comparison
      │
      ├──────────> Analytics
      │
      └──────────> AI/Search
                       │
                       ▼
                PartSearchDocument
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
          Filters   Similarity  Embedding
             │         │         │
             └─────────┴─────────┘
                       │
                       ▼
                  AI reasoning
```

The core Modelryx rule is:

> **Workers understand CAD systems. Modelryx understands engineering metadata. AI understands Modelryx engineering/search data.**

This separation allows a FreeCAD-generated part, an external CAD part, and a STEP part representing essentially the same engineering geometry to participate in the same search and similarity pipeline without CAD-specific logic in the AI layer.
