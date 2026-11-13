# Fresh database initialization

This release initializes the current schema directly. It does not contain an
incremental database upgrade history or commands to upgrade old installations.

## Docker Compose

Configure the required application environment, then start the application:

```bash
docker compose up --build -d
```

The one-shot `initialize-db` service waits for PostgreSQL, creates the pgvector
extension, creates the schema, and seeds one selected numbering template. The API
and processing workers wait for successful initialization. Their startup does not
run schema creation itself. To rerun initialization explicitly:

```bash
docker compose run --rm initialize-db
```

## Local Python

Use PostgreSQL with pgvector installed on the database host. The configured
database role must be able to create the extension and application tables. Install
the server and worker SDK dependencies, configure `MODELRYX_DATABASE_URL`, and run:

```bash
cd server
python -m modelryx.initialize_database
```

Plain `postgresql://` URLs use the installed psycopg 3 driver. Initialization uses
a single transaction and a PostgreSQL advisory lock to serialize concurrent calls.
An interrupted or failed initialization rolls back its schema and seed changes.

## Schema ownership and repeat runs

SQLAlchemy models are the source of truth for tables, foreign keys, check
constraints, and database defaults. `modelryx/models/indexes.py` registers the
PostgreSQL expression, compound, partial, GIN, and HNSW indexes in the same metadata.
The `database_schema` table records a deterministic fingerprint of these declarations.

The first initialization requires an empty application schema. Subsequent calls
with the same recorded fingerprint and table set do nothing: numbering counters,
administrator settings, and user data remain unchanged. Initialization rejects
an existing unrecognized schema, a different fingerprint, or missing/extra tables.
It never drops data, adds missing tables to an existing installation, or upgrades
an old schema. Manual database edits are not a supported schema-management workflow.

The selected default numbering template uses `PART-{sequence:06d}` and
`ASM-{sequence:06d}`, each starting at 1. The administrator account is provisioned
through the existing authentication bootstrap flow, not by copying historical data.

During development, model changes that alter the schema require a new, empty
development database. Data-preserving upgrades after deployment require a separately
designed and tested upgrade process. Do not run this release against a historical
database expecting an automatic conversion. Restored databases must match the
application release and its recorded schema fingerprint.

## Verification

```bash
docker compose --profile test run --build --rm test
```

The test profile uses a separate `modelryx_test` database, resets only that test
schema, and runs `initialize-test-db` before pytest. Initializer integration tests
cover repeat execution, concurrent startup, transaction rollback, incompatible
schema rejection, search indexes, and uniqueness enforcement.
