# Backup and recovery

The supplied Compose deployment stores metadata in PostgreSQL (`postgres-data`)
and CAD files, attachments, and generated artifacts in `object-data`, mounted at
`/var/lib/modelryx/objects`. Both volumes are required for recovery.
The application uses `LocalStorageProvider` for file storage.
Cleanup policies are not backups.

## Create a consistent backup

Run from the repository root with the deployment's `.env` available. Use a
maintenance window: prevent new requests at your ingress, allow active uploads
and conversions to finish, then stop all writers. Also stop other API clients/controllers or maintenance processes that can write to this
database or object volume. Independent external CAD workers do not have database/object access.
These Bash commands require Docker Compose and enough space for all objects.

```bash
set -euo pipefail
umask 077
backup_dir="$(pwd)/backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
# List only writers enabled in this deployment; add embedded FreeCAD services if used.
writers=(api general-worker)
docker compose stop "${writers[@]}"
# If a backup fails, keep writers stopped and investigate before restarting.
docker compose exec -T db pg_dump -U modelryx -d modelryx --format=custom > "$backup_dir/database.dump"
docker compose run --rm --no-deps -T --entrypoint tar api -C /var/lib/modelryx/objects -czf - . > "$backup_dir/objects.tar.gz"
test -s "$backup_dir/database.dump"
gzip -t "$backup_dir/objects.tar.gz"
sha256sum "$backup_dir/database.dump" "$backup_dir/objects.tar.gz" > "$backup_dir/SHA256SUMS"
docker compose start "${writers[@]}"
```

Check service health before reopening ingress. Retain the two backup files together
with the source revision and schema fingerprint. Keep configuration and secrets
securely and separately. Copy backups off the host to encrypted storage with a
retention policy. Do not commit them to Git; `/backups/` is ignored.
Do not delete older backups until the new pair passes a recovery drill.

## Restore into an isolated deployment

Use a separate Compose project with empty database and object volumes, matching
source revision, and isolated ports/network. Do not restore into a live deployment.
Verify checksums before restoring. From the target deployment directory:

```bash
# Start only the empty target database and initialize object ownership.
docker compose up -d --wait db
docker compose run --rm --no-deps storage-init
# Restore a matched database/object backup pair.
docker compose exec -T db pg_restore -U modelryx -d modelryx --exit-on-error --no-owner < /absolute/path/to/backup/database.dump
docker compose run --rm --no-deps -T --user 0:0 --entrypoint sh api -c 'tar -xzf - -C /var/lib/modelryx/objects && chown -R 100:101 /var/lib/modelryx/objects' < /absolute/path/to/backup/objects.tar.gz
# Start the intended topology; this example keeps embedded FreeCAD disabled.
docker compose up -d api general-worker step-worker
```

Check `/health/ready`, sign in, download representative original files and
attachments, and load previews. Check revision history and assembly references.
Record recovery time and results before relying on the procedure in production.

## Database-only utilities

`scripts/backup-database.sh [backup_dir] [retention_days]` creates compressed SQL
using host `pg_dump` and exported `POSTGRES_*` variables. It does not load `.env`.
Optional S3 upload uses `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `S3_BUCKET`.
It does not back up objects or coordinate a consistent application snapshot.
Explicitly pass a directory under `backups/`; the default is the current directory.

`scripts/verify-backups.sh [backup_dir]` checks those compressed SQL files. It does
not perform a database restore or verify objects. A successful database/object
recovery drill is the required evidence of recoverability.
