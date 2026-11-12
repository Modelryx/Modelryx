# Backup and Disaster Recovery Strategy

This document describes Modelryx's backup approach for production deployments, including automated backup scripts, retention policies, and recovery procedures.

## Overview

Modelryx uses a three-tier backup strategy to ensure data safety and minimize recovery time objective (RTO) and recovery point objective (RPO):

| Tier | Component | Location | Frequency | Retention |
|------|-----------|----------|-----------|-----------|
| 1 | Database backups | Local filesystem | Hourly (configurable) | 30 days |
| 2 | Object storage (CAD files) | MinIO automatic cleanup | Continuous | 30 days (processing), 7 days (uploads) |
| 3 | Offsite backup | S3/AWS Glacier | Daily (configurable) | 90 days |

## Database Backups

### Automated Backup Script

The [`scripts/backup-database.sh`](../scripts/backup-database.sh) script performs full PostgreSQL database backups with automatic compression and retention management.

**Features:**
- Full SQL dump using `pg_dump`
- gzip compression (typically 10-20% of original size)
- Automatic retention policy enforcement
- Optional S3 upload for offsite storage
- Colorized console output
- Comprehensive logging

### Usage

```bash
# One-time backup
./scripts/backup-database.sh

# Backup with S3 upload (requires AWS credentials)
BACKUP_UPLOAD_S3=true ./scripts/backup-database.sh

# Verify before cleanup (interactive)
./scripts/backup-database.sh

# Customize retention (default 30 days)
BACKUP_RETENTION_DAYS=90 ./scripts/backup-database.sh
```

### Environment Variables

```bash
# Required (must be set in your shell or .env)
POSTGRES_HOST=postgres            # Default: localhost
POSTGRES_PORT=5432                # Default: 5432
POSTGRES_DB=modelryx              # Default: modelryx
POSTGRES_USER=modelryx            # Default: modelryx
POSTGRES_PASSWORD=<your-password> # Must be set

# Optional
BACKUP_DIR=/backups               # Default: ./backups
BACKUP_RETENTION_DAYS=30          # Default: 30
BACKUP_UPLOAD_S3=false            # Default: false (set to true to enable)
AWS_PROFILE=default               # For S3 upload
AWS_REGION=us-east-1              # For S3 upload
S3_BACKUP_BUCKET=modelryx-backups # For S3 upload
S3_STORAGE_CLASS=GLACIER          # Cost-optimized (retrieval time: 1-5 minutes)
```

### Backup Filename Format

Backups are named with timestamps: `modelryx_backup_YYYYMMDD_HHMMSS.sql.gz`

Example: `modelryx_backup_20261111_143022.sql.gz`

This format enables:
- Easy chronological sorting
- Quick identification of when backup was taken
- Unique naming preventing overwrites

### Disk Space Estimation

For typical deployments:

```
Database size: ~500 MB
Compressed backup: ~50-100 MB per backup
Monthly storage: 30 backups × 75 MB = 2.25 GB
```

Allocate at least **10 GB** of disk space for 30-day retention with headroom.

## Database Restore

### Using the Restore Script

The [`scripts/restore-database.sh`](../scripts/restore-database.sh) script safely restores from backups with safety confirmations.

```bash
# Interactive restore (prompts for confirmation)
./scripts/restore-database.sh

# Specify backup file explicitly
./scripts/restore-database.sh modelryx_backup_20261111_143022.sql.gz

# Restore from S3
./scripts/restore-database.sh s3://modelryx-backups/modelryx_backup_20261111_143022.sql.gz
```

**Safety features:**
- Lists available backups for selection
- Shows backup size and creation time
- Requires explicit confirmation before restore
- Drops existing data (safe in isolated environment)
- Validates restore integrity
- Reports total records restored

### Manual Restore

If using the script is not possible:

```bash
# For local backup
gunzip -c ./backups/modelryx_backup_20261111_143022.sql.gz | \
  psql -h localhost -U modelryx -d modelryx

# For S3 backup
aws s3 cp s3://modelryx-backups/modelryx_backup_20261111_143022.sql.gz - | \
  gunzip -c | \
  psql -h localhost -U modelryx -d modelryx
```

### Restore Verification

After restore, verify data integrity:

```bash
# Check total row counts
psql -h localhost -U modelryx -d modelryx -c "\
  SELECT table_name, \
         (xpath('/row/Count/text()', query_to_xml(
           'SELECT count(*) FROM ' || table_name, false, true, '')))[1]::text::int 
  FROM information_schema.tables 
  WHERE table_schema = 'public';"

# Check specific tables
psql -h localhost -U modelryx -d modelryx -c "SELECT COUNT(*) as item_count FROM items;"
psql -h localhost -U modelryx -d modelryx -c "SELECT COUNT(*) as revision_count FROM item_revisions;"
```

## Object Storage Backups

### MinIO Lifecycle Policies

MinIO automatically cleans up old objects based on lifecycle policies defined in [`scripts/minio-lifecycle-policy.json`](../scripts/minio-lifecycle-policy.json).

**Default retention:**
- `processing/` prefix: 30 days (temporary worker data)
- `uploads/` prefix: 7 days (staged but not confirmed uploads)
- `artifacts/` prefix: No automatic deletion (permanent storage)

These are applied automatically when MinIO starts via the `minio-init` service in `compose.yaml`.

### Modifying Retention Policies

To change retention periods:

1. Edit `scripts/minio-lifecycle-policy.json`
2. Restart MinIO services:

```bash
docker compose restart minio minio-init
```

3. Verify policies applied:

```bash
docker compose exec minio mc ilm ls minio/modelryx
```

## Backup Verification

### Using the Verification Script

The [`scripts/verify-backups.sh`](../scripts/verify-backups.sh) script validates backup integrity and retention policies:

```bash
# Verify all local backups
./scripts/verify-backups.sh

# Verify S3 backups
VERIFY_S3=true ./scripts/verify-backups.sh

# Check specific backup file
./scripts/verify-backups.sh modelryx_backup_20261111_143022.sql.gz
```

**Verification includes:**
- File integrity checks (file size, gzip magic bytes)
- Compression validation (attempts decompression)
- Retention policy compliance (age checking)
- SQL validity scan (samples headers/footers)
- S3 bucket connectivity (if enabled)
- Disk space warnings

### Manual Verification

```bash
# Check backup file size and age
ls -lh ./backups/modelryx_backup_*.sql.gz

# Test decompression without extracting
gunzip -t ./backups/modelryx_backup_20261111_143022.sql.gz && echo "OK"

# Count SQL statements in backup
gunzip -c ./backups/modelryx_backup_20261111_143022.sql.gz | \
  grep -c "^INSERT INTO"
```

## Automated Scheduling

### Linux/macOS (Cron)

Create `/etc/cron.d/modelryx-backup` or add to your user crontab:

```bash
# Hourly backups at :00 of every hour
0 * * * * /path/to/modelryx/scripts/backup-database.sh >> /var/log/modelryx-backup.log 2>&1

# Daily S3 upload at 2:00 AM
0 2 * * * BACKUP_UPLOAD_S3=true /path/to/modelryx/scripts/backup-database.sh >> /var/log/modelryx-backup.log 2>&1

# Weekly verification on Sunday at 3:00 AM
0 3 * * 0 /path/to/modelryx/scripts/verify-backups.sh >> /var/log/modelryx-verify.log 2>&1
```

Make scripts executable:
```bash
chmod +x /path/to/modelryx/scripts/*.sh
```

### Windows (Task Scheduler)

Create a scheduled task using Task Scheduler:

1. Open Task Scheduler
2. Create Basic Task → Name: "Modelryx Backup"
3. Trigger: Daily at 2:00 AM (or hourly for frequent backups)
4. Action: Start a program
   - Program: `C:\Program Files\Git\bin\bash.exe`
   - Arguments: `C:\path\to\modelryx\scripts\backup-database.sh`
   - Start in: `C:\path\to\modelryx`
5. Set environment variables in script or `.env` file

### Docker Compose with Volume Mounts

For containerized deployment, mount backup directory and run backups as a scheduled job:

```yaml
# In compose.yaml
services:
  backup-worker:
    image: postgres:15-alpine
    volumes:
      - ./scripts:/scripts:ro
      - ./backups:/backups
      - .env:/env:ro
    entrypoint: /bin/sh
    command: -c "source /env && /scripts/backup-database.sh"
    restart: "no"
    # Scheduled via external orchestrator (Kubernetes CronJob, etc.)
```

## Disaster Recovery Plan

### RTO and RPO Targets

- **RTO (Recovery Time Objective):** 30 minutes
- **RPO (Recovery Point Objective):** 1 hour

### Recovery Procedures

#### Scenario 1: Database Corruption (Partial)

1. Stop Modelryx services:
   ```bash
   docker compose down
   ```

2. Find latest good backup:
   ```bash
   ls -lt ./backups/modelryx_backup_*.sql.gz | head -5
   ```

3. Restore from backup:
   ```bash
   ./scripts/restore-database.sh
   ```

4. Restart services:
   ```bash
   docker compose up -d
   ```

5. Verify application health:
   ```bash
   curl http://localhost:8000/health
   ```

#### Scenario 2: Complete Database Loss

1. Ensure PostgreSQL is running with clean database:
   ```bash
   docker compose up -d postgres
   ```

2. Restore from latest backup (same as Scenario 1)

3. Verify all tables present:
   ```bash
   docker compose exec postgres psql -U modelryx -d modelryx -c "\dt"
   ```

#### Scenario 3: Offsite Recovery from S3

If local backups are unavailable:

```bash
# List available S3 backups
aws s3 ls s3://modelryx-backups/ --recursive

# Download specific backup
aws s3 cp s3://modelryx-backups/modelryx_backup_20261111_143022.sql.gz ./backups/

# Restore using downloaded backup
./scripts/restore-database.sh ./backups/modelryx_backup_20261111_143022.sql.gz
```

#### Scenario 4: Object Storage Recovery

CAD files in MinIO are protected by lifecycle policies and versioning (when enabled).

To recover a deleted object:

```bash
# Enable versioning on MinIO bucket (if not already enabled)
docker compose exec minio mc version enable minio/modelryx

# List versions of object
docker compose exec minio mc ls --versions minio/modelryx/artifacts/item-id/

# Restore deleted object
docker compose exec minio mc cp minio/modelryx/artifacts/item-id/\?versionId=<version-id> \
  minio/modelryx/artifacts/item-id/file.step
```

## Monitoring and Alerts

### Log Files

Monitor backup logs for failures:

```bash
# View recent backup logs
tail -f /var/log/modelryx-backup.log

# Check for errors
grep -i error /var/log/modelryx-backup.log

# View last 10 successful backups
grep "Backup completed successfully" /var/log/modelryx-backup.log | tail -10
```

### Health Checks

Add health check to monitoring system:

```bash
#!/bin/bash
# check-backups.sh - For Nagios/Icinga/Prometheus

BACKUP_DIR="/backups"
MAX_AGE_HOURS=25  # Alert if no backup in last 25 hours

LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/modelryx_backup_*.sql.gz 2>/dev/null | head -1)

if [ -z "$LATEST_BACKUP" ]; then
    echo "CRITICAL: No backups found"
    exit 2
fi

BACKUP_AGE=$(($(date +%s) - $(stat -f%m "$LATEST_BACKUP" 2>/dev/null || stat -c%Y "$LATEST_BACKUP")))
BACKUP_AGE_HOURS=$((BACKUP_AGE / 3600))

if [ "$BACKUP_AGE_HOURS" -gt "$MAX_AGE_HOURS" ]; then
    echo "CRITICAL: Last backup is $BACKUP_AGE_HOURS hours old"
    exit 2
fi

echo "OK: Latest backup is $BACKUP_AGE_HOURS hours old"
exit 0
```

### Dashboard Metrics

Track these metrics in your monitoring system:

- Last backup timestamp
- Backup file size (MB)
- Backup duration (seconds)
- Compression ratio (%)
- S3 upload success rate (%)
- Database table counts
- MinIO object counts by prefix
- Disk space used by backups (%)

## Best Practices

1. **Test restores regularly** - At least monthly, perform a restore to non-production environment
2. **Offsite backups** - Always maintain geographically separate backups
3. **Retention policy** - Balance storage costs with recovery needs
4. **Monitoring** - Alert on backup failures within 1 hour
5. **Documentation** - Keep runbooks updated for your team
6. **Access control** - Restrict backup and restore permissions
7. **Encryption** - Use S3 server-side encryption and HTTPS for backups
8. **Versioning** - Enable database backup versioning for accidental overwrites
9. **Scaling** - Adjust `BACKUP_RETENTION_DAYS` as data grows
10. **Automation** - Use cron/Task Scheduler to eliminate manual backup steps

## Troubleshooting

### Backup Script Fails

```bash
# Check PostgreSQL connectivity
psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT 1;"

# Check disk space
df -h ./backups

# View script output
./scripts/backup-database.sh -v  # Verbose mode (if supported)
```

### Restore Fails

```bash
# Verify backup file integrity
gunzip -t ./backups/modelryx_backup_*.sql.gz

# Check PostgreSQL is running
docker compose ps postgres

# Check PostgreSQL logs
docker compose logs postgres | tail -50
```

### S3 Upload Fails

```bash
# Verify AWS credentials
aws s3 ls

# Check bucket permissions
aws s3api head-bucket --bucket modelryx-backups

# Verify network connectivity
nc -zv s3.amazonaws.com 443
```

## Support and Further Reading

- [PostgreSQL Backup Documentation](https://www.postgresql.org/docs/current/backup.html)
- [MinIO Lifecycle Rules](https://min.io/docs/minio/linux/administration/object-management/object-retention/object-retention.html)
- [AWS S3 Glacier Storage](https://aws.amazon.com/s3/storage-classes/glacier/)
- [Disaster Recovery Planning](docs/DEPLOYMENT.md#disaster-recovery)

---

**Last Updated:** 2026-11-11
**Maintainer:** Modelryx Team
**Related:** [DEPLOYMENT.md](DEPLOYMENT.md), [DEVELOPMENT.md](../DEVELOPMENT.md)
