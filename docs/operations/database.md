# Database reference

Compose uses PostgreSQL **17** with pgvector. SQLAlchemy models under
`server/src/server/modelryx/models` are authoritative for tables, indexes and constraints.
This document does not duplicate SQL definitions that can drift from those models.

## Initialization

`python -m modelryx.initialize_database` creates a fresh PostgreSQL schema, records its fingerprint
and seeds numbering templates. Re-runs verify the same schema. It refuses an incompatible schema;
it does not migrate, drop or reset it. There is no shipped Alembic migration workflow. See
[database initialization](database-initialization.md) before deploying changed model definitions.

The Compose service is `db`, application database/user are `modelryx`, and the connection setting
is `MODELRYX_DATABASE_URL`. Compose uses `POSTGRES_PASSWORD` for the database and URL interpolation.
Host Python uses the mapped localhost port, not container hostname `db`.

```powershell
docker compose exec db psql -U modelryx -d modelryx
```

Use PostgreSQL inspection (`\dt`, `\d`) or SQLAlchemy metadata for current definitions. Do not
create tables from old copied DDL. Keep real connection strings in ignored environment files.

## Domain boundaries

- Users/roles identify authenticated principals. Usernames are external subjects; password hashes
  use PBKDF2-SHA256, with lockout/authentication-version state.
- Items identify engineering records; revisions carry lifecycle/configuration state. Immutable
  content snapshots bind authoritative/derived artifacts to content history.
- Artifacts/files/revision relations retain hashes, storage keys, byte counts and generator lineage.
  File bytes live outside PostgreSQL.
- Managed BOM views/lines retain child revision identity, occurrences, quantities and placement.
  Native CAD component evidence does not automatically create managed relationships.
- Processing jobs/heartbeats track orchestration. Independent HTTP workers have separate job
  journals and no Modelryx database access.
- Checkouts, approvals, audit, validation, search and optional AI/index records have their own
  mapped entities; use the models/OpenAPI schemas for exact fields.

Application storage uses `LocalStorageProvider`. Back up PostgreSQL and `object-data` together;
one alone is not a complete recovery. See [backup/recovery](backup-strategy.md). The optional S3
helper is not a selectable application storage backend.

## Tests

The test database is `modelryx_test`. Tests require test mode and reject the application database.
Test Compose reset services intentionally reset their isolated schema. Use a separate project as
shown in [testing](../development/testing.md). Fresh-schema tests do not prove upgrade compatibility.
