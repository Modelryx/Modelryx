# Architecture Documentation

Core technical documentation for Modelryx's platform design, data model, and processing framework.

## Essential Reading

- **[platform-architecture.md](./platform-architecture.md)** — Top-level system design, component boundaries, persistent data, and primary request flows
- **[partmetadata-v1.md](./partmetadata-v1.md)** — CAD-neutral metadata specification for parts and assemblies, normalization rules, and responsibility boundaries
- **[cad-processing-framework.md](./cad-processing-framework.md)** — CAD processing pipelines, worker coordination, artifact validation, and lifecycle management

## Quick Reference

### System Components

```
React Frontend ←→ FastAPI Server ←→ PostgreSQL + pgvector
                  (with background workers)
                      ↓↓
              STEP Worker + FreeCAD
              (CAD processing)
```

### Data Model

Modelryx's core entities:

- **Item** — Unique product identifier with versioning and lifecycle management
- **Revision** — Lifecycle-controlled engineering revision with immutable content snapshots
- **Artifact** — Classified content container linked to files and revision snapshots
- **PartMetadata** — Normalized, CAD-system-neutral engineering data (schema, features, properties)

### Processing Pipeline

1. **Upload & Staging** — User uploads CAD files to a staging area
2. **Validation** — Duplicate detection, metadata extraction, initial checks
3. **Conversion** — STEP worker and FreeCAD workers process geometry and metadata
4. **Publication** — Atomic commit of metadata, artifacts, and search indexes
5. **Indexing** — AI and knowledge index creation (asynchronous, independent from CAD result)

## See Also

- [AI Documentation](../ai/) — AI capabilities and integrations
- [Workers](../workers/) — Worker APIs and integration guides
