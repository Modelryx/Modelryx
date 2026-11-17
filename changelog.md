# Changelog

Changes for the first public release are tracked under Unreleased. Package versions
currently identify the development series as 0.1.0; a public release version and
date have not yet been assigned. Earlier pre-publication notes were development
summaries, not evidence of a shipped 1.0.0 release.

## [Unreleased]

### Added

- CAD data management API and React/TypeScript frontend, with PostgreSQL/pgvector
  and persistent local object storage.
- Built-in FreeCAD and STEP processing, plus an external CAD worker contract and
  a worker SDK. Proprietary worker implementations are maintained separately.
- Optional OpenAI-compatible gateway configuration for AI-assisted features.
- PowerShell project configuration helper and manual environment setup guidance.
- Third-party license reference collection, notices, and release packaging guidance.
- Frontend license notices delivered with the production build.

### Changed

- Reviewed setup, API, security, database, and worker documentation against current
  source and corrected obsolete protocol and migration examples.
- Clarified external versus embedded FreeCAD processing and CAD-session limits.

### Fixed

- Wait for pending TechDraw background work before closing native documents in
  extraction, controlled CAD edits, and dependency preprocessing. This prevents
  base-view objects being freed while detail-view tasks still use them.
- Bind the frontend development server to loopback by default; trusted LAN access
  requires an explicit host override.
- Pin the STEP worker Miniforge base by release tag and digest.

### Removed

- Database backup, restore, and backup-verification scripts. Built-in backup and
  restore support is deferred beyond the first public release.

### Limitations

- Database initialization creates a fresh schema or verifies a matching schema;
  there is no shipped Alembic migration or automatic upgrade workflow.
- Worker authentication uses the contract's shared-key mechanism. The platform
  does not claim HMAC request signing or cryptographic replay prevention.
- Native CAD output support depends on qualified worker runtimes. A health check
  alone does not establish conversion correctness or native licensing suitability.
- Third-party reference license texts are not a complete binary inventory. Image
  and installer publication requires artifact-specific notice and source review.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and contribution guidance.
See [SECURITY_POLICY.md](SECURITY_POLICY.md) for private vulnerability reporting.
