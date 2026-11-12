# Database Schema and Reference

This guide covers the Modelryx database schema, entity relationships, and data access patterns.

## Database Overview

- **System:** PostgreSQL 14+ with pgvector extension
- **ORM:** SQLAlchemy (Python)
- **Migrations:** Alembic
- **Connection string:** `postgresql://user:password@host:port/modelryx_db`

## Core Entities

### users

Represents user accounts in Modelryx.

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY,
  email VARCHAR(255) UNIQUE NOT NULL,
  hashed_password VARCHAR(255) NOT NULL,
  name VARCHAR(255),
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_email ON users(email);
```

Fields:
- `id` — UUID, primary key
- `email` — Email address (unique)
- `hashed_password` — Bcrypt hashed password
- `name` — Display name
- `is_active` — Account status
- `created_at` — Account creation timestamp
- `updated_at` — Last modification timestamp

### workspaces

Represents project/team workspaces.

```sql
CREATE TABLE workspaces (
  id UUID PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_workspaces_owner ON workspaces(owner_id);
CREATE INDEX idx_workspaces_created ON workspaces(created_at DESC);
```

Fields:
- `id` — UUID, primary key
- `name` — Workspace name
- `description` — Workspace description
- `owner_id` — Owner user ID (foreign key)
- `created_at` — Creation timestamp
- `updated_at` — Last modification timestamp

### workspace_members

Many-to-many relationship between users and workspaces.

```sql
CREATE TABLE workspace_members (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role VARCHAR(50) NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  
  UNIQUE(workspace_id, user_id)
);

CREATE INDEX idx_workspace_members_workspace ON workspace_members(workspace_id);
CREATE INDEX idx_workspace_members_user ON workspace_members(user_id);
```

Fields:
- `id` — UUID, primary key
- `workspace_id` — Workspace ID (foreign key)
- `user_id` — User ID (foreign key)
- `role` — Access level: `owner`, `editor`, `viewer`
- `created_at` — Membership creation timestamp

### items

Represents CAD models and assemblies.

```sql
CREATE TABLE items (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  item_type VARCHAR(50) NOT NULL CHECK (item_type IN ('part', 'assembly', 'drawing')),
  created_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_items_workspace ON items(workspace_id);
CREATE INDEX idx_items_created_by ON items(created_by);
CREATE INDEX idx_items_name ON items(name);
CREATE INDEX idx_items_updated ON items(updated_at DESC);
```

Fields:
- `id` — UUID, primary key
- `workspace_id` — Workspace ID (foreign key)
- `name` — Item name/title
- `description` — Item description
- `item_type` — Type: `part`, `assembly`, or `drawing`
- `created_by` — Creator user ID (foreign key)
- `created_at` — Creation timestamp
- `updated_at` — Last modification timestamp

### item_revisions

Tracks versions of items.

```sql
CREATE TABLE item_revisions (
  id UUID PRIMARY KEY,
  item_id UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  revision_number INTEGER NOT NULL,
  created_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  change_message TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  
  UNIQUE(item_id, revision_number)
);

CREATE INDEX idx_item_revisions_item ON item_revisions(item_id);
CREATE INDEX idx_item_revisions_created ON item_revisions(created_at DESC);
```

Fields:
- `id` — UUID, primary key
- `item_id` — Item ID (foreign key)
- `revision_number` — Incremental revision number
- `created_by` — Creator user ID (foreign key)
- `created_at` — Revision creation timestamp
- `change_message` — Description of changes
- `metadata` — JSONB containing:
  - `geometry` — Geometric data and bounding box
  - `assembly_tree` — Part hierarchy and references
  - `properties` — Material, mass, cost, etc.
  - `documentation` — Design notes, specifications

### files

Stores attachments (CAD files, images, documents).

```sql
CREATE TABLE files (
  id UUID PRIMARY KEY,
  revision_id UUID NOT NULL REFERENCES item_revisions(id) ON DELETE CASCADE,
  filename VARCHAR(255) NOT NULL,
  file_type VARCHAR(50) NOT NULL,
  size_bytes INTEGER NOT NULL,
  storage_key VARCHAR(255) NOT NULL UNIQUE,
  uploaded_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_files_revision ON files(revision_id);
CREATE INDEX idx_files_created ON files(created_at DESC);
CREATE INDEX idx_files_type ON files(file_type);
```

Fields:
- `id` — UUID, primary key
- `revision_id` — Revision ID (foreign key)
- `filename` — Original filename
- `file_type` — File type (step, iges, stl, pdf, etc.)
- `size_bytes` — File size in bytes
- `storage_key` — Object storage key (S3/MinIO)
- `uploaded_by` — Uploader user ID (foreign key)
- `created_at` — Upload timestamp

### cad_processing_jobs

Tracks CAD worker processing jobs.

```sql
CREATE TABLE cad_processing_jobs (
  id UUID PRIMARY KEY,
  revision_id UUID NOT NULL REFERENCES item_revisions(id) ON DELETE CASCADE,
  worker_type VARCHAR(50) NOT NULL,
  status VARCHAR(50) NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
  input_file_id UUID REFERENCES files(id),
  output_metadata JSONB DEFAULT '{}'::jsonb,
  error_message TEXT,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_cad_jobs_revision ON cad_processing_jobs(revision_id);
CREATE INDEX idx_cad_jobs_status ON cad_processing_jobs(status);
CREATE INDEX idx_cad_jobs_created ON cad_processing_jobs(created_at DESC);
```

Fields:
- `id` — UUID, primary key
- `revision_id` — Revision being processed (foreign key)
- `worker_type` — Worker that processed it (freecad, step, etc.)
- `status` — Processing state
- `input_file_id` — Input file (foreign key)
- `output_metadata` — Processing result (geometry, assembly tree, etc.)
- `error_message` — Failure reason if status=failed
- `started_at` — Processing start timestamp
- `completed_at` — Processing finish timestamp
- `created_at` — Job creation timestamp

## Querying Patterns

### Get Latest Revision of an Item

```sql
SELECT ir.* FROM item_revisions ir
WHERE ir.item_id = $1
ORDER BY ir.revision_number DESC
LIMIT 1;
```

### Find Items by Workspace with Latest Revision

```sql
SELECT 
  i.id, i.name, i.item_type,
  ir.id as latest_revision_id,
  ir.revision_number,
  ir.created_at as revision_created_at
FROM items i
LEFT JOIN item_revisions ir ON i.id = ir.item_id AND ir.revision_number = (
  SELECT MAX(revision_number) FROM item_revisions WHERE item_id = i.id
)
WHERE i.workspace_id = $1
ORDER BY i.updated_at DESC;
```

### Search Items by Name or Metadata

```sql
SELECT i.* FROM items i
WHERE i.workspace_id = $1
  AND (
    i.name ILIKE '%' || $2 || '%'
    OR i.description ILIKE '%' || $2 || '%'
    OR i.id IN (
      SELECT item_id FROM item_revisions 
      WHERE metadata::text ILIKE '%' || $2 || '%'
    )
  )
ORDER BY i.updated_at DESC;
```

### Find Pending CAD Processing Jobs

```sql
SELECT cpj.* FROM cad_processing_jobs cpj
WHERE cpj.status = 'pending'
  OR (cpj.status = 'processing' AND cpj.started_at < now() - interval '1 hour')
ORDER BY cpj.created_at ASC;
```

### Get Assembly Tree for Item

```sql
SELECT 
  ir.metadata->'assembly_tree' as assembly_tree,
  ir.metadata->'geometry' as geometry
FROM item_revisions ir
WHERE ir.item_id = $1
  AND ir.revision_number = (
    SELECT MAX(revision_number) FROM item_revisions WHERE item_id = $1
  );
```

## Migration Best Practices

### Creating a Migration

```bash
# Auto-generate migration based on model changes
python -m alembic revision --autogenerate -m "add user preferences table"

# Review generated migration in migrations/versions/
# Edit if needed (auto-generate sometimes needs manual adjustment)

# Apply migration
python -m alembic upgrade head
```

### Migration Template

```python
"""Add user preferences table

Revision ID: abc123def456
Revises: xyz789uvw012
Create Date: 2026-11-11 13:40:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'abc123def456'
down_revision = 'xyz789uvw012'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'user_preferences',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('theme', sa.VARCHAR(50), default='light'),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index('idx_user_preferences_user', 'user_preferences', ['user_id'])

def downgrade():
    op.drop_index('idx_user_preferences_user', 'user_preferences')
    op.drop_table('user_preferences')
```

## Performance Optimization

### Indexing Strategy

Create indexes for:
- **Foreign keys** — Enable joins (auto-indexed via SQLAlchemy relationships)
- **Frequent filters** — e.g., `workspace_id`, `created_by`
- **Sorting columns** — e.g., `created_at DESC`, `name ASC`
- **Full-text search** — Future: use PostgreSQL full-text search

Current indexes cover common queries. Monitor slow queries:

```sql
-- Enable query logging
ALTER SYSTEM SET log_min_duration_statement = 1000;  -- Log queries > 1 second
SELECT pg_reload_conf();

-- View slow query log
tail -f /var/log/postgresql/postgresql.log | grep "duration:"
```

### Query Optimization

```sql
-- Use EXPLAIN to analyze queries
EXPLAIN ANALYZE
SELECT i.* FROM items i
WHERE i.workspace_id = $1
  AND i.name ILIKE '%motor%';

-- Look for "Seq Scan" warnings; add index if needed
CREATE INDEX idx_items_workspace_name ON items(workspace_id, name);
```

### Connection Pooling

Managed by SQLAlchemy connection pool:
- **Pool size:** 20 connections
- **Max overflow:** 10 connections
- **Timeout:** 30 seconds

Tune in `server/src/server/config.py` if needed.

## Data Retention

### Archive Policies

- **Items:** Retained indefinitely
- **Revisions:** Retained indefinitely (version control)
- **Files:** Retained per retention policy (typically 1-5 years)
- **Processing jobs:** Retained 90 days (for audit trail)
- **Access logs:** Retained 30 days

Implement archive cleanup:

```python
# Archive old processing jobs
DELETE FROM cad_processing_jobs 
WHERE completed_at < now() - interval '90 days';
```

## Backup and Recovery

See [DEPLOYMENT.md](./DEPLOYMENT.md#backup-strategy) for backup procedures.

## Related Documentation

- **Architecture:** [docs/architecture/partmetadata-v1.md](../architecture/partmetadata-v1.md)
- **API Reference:** [docs/API.md](./API.md)
- **Development:** [DEVELOPMENT.md](../../DEVELOPMENT.md)

---

Last updated: 2026-11-11
