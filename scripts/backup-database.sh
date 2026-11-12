#!/bin/bash
# Modelryx Database Backup Script
# Automated backup of PostgreSQL database with compression and retention policy
# 
# Usage: ./backup-database.sh [backup_dir] [retention_days]
# Example: ./backup-database.sh /backups 30
#
# Environment variables:
#   POSTGRES_HOST       - Database host (default: localhost)
#   POSTGRES_PORT       - Database port (default: 5432)
#   POSTGRES_USER       - Database user (default: modelryx)
#   POSTGRES_DB         - Database name (default: modelryx)
#   POSTGRES_PASSWORD   - Database password (required)
#   AWS_ACCESS_KEY_ID   - Optional S3 access key for remote backup
#   AWS_SECRET_ACCESS_KEY - Optional S3 secret for remote backup
#   S3_BUCKET           - Optional S3 bucket for remote backup

set -euo pipefail

# Configuration
BACKUP_DIR="${1:-.}"
RETENTION_DAYS="${2:-30}"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_FILE="${BACKUP_DIR}/modelryx_backup_${TIMESTAMP}.sql.gz"
BACKUP_LOG="${BACKUP_DIR}/modelryx_backup_${TIMESTAMP}.log"

# Database configuration
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-modelryx}"
POSTGRES_DB="${POSTGRES_DB:-modelryx}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$BACKUP_LOG"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} [$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$BACKUP_LOG" >&2
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} [$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$BACKUP_LOG"
}

# Validation
if [ ! -d "$BACKUP_DIR" ]; then
    log_error "Backup directory does not exist: $BACKUP_DIR"
    exit 1
fi

if [ -z "${POSTGRES_PASSWORD:-}" ]; then
    log_error "POSTGRES_PASSWORD environment variable is required"
    exit 1
fi

# Start backup
log "Starting Modelryx database backup..."
log "Database: $POSTGRES_DB"
log "Host: $POSTGRES_HOST:$POSTGRES_PORT"
log "Backup file: $BACKUP_FILE"
log "Retention: $RETENTION_DAYS days"

# Perform backup
export PGPASSWORD="$POSTGRES_PASSWORD"

if pg_dump \
    -h "$POSTGRES_HOST" \
    -p "$POSTGRES_PORT" \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    --no-password \
    --verbose \
    --format=plain \
    2>> "$BACKUP_LOG" | gzip > "$BACKUP_FILE"; then
    
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    log_success "Database backup completed"
    log "Backup file size: $BACKUP_SIZE"
    
else
    log_error "Database backup failed. Check $BACKUP_LOG for details."
    exit 1
fi

# Upload to S3 if configured
if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${S3_BUCKET:-}" ]; then
    log "Uploading backup to S3 bucket: $S3_BUCKET"
    
    if aws s3 cp "$BACKUP_FILE" "s3://${S3_BUCKET}/modelryx-backups/$(basename "$BACKUP_FILE")" \
        --region "${AWS_REGION:-us-east-1}" \
        --storage-class GLACIER \
        2>> "$BACKUP_LOG"; then
        log_success "Backup uploaded to S3"
    else
        log_error "S3 upload failed. Backup file saved locally at $BACKUP_FILE"
    fi
fi

# Cleanup old local backups
log "Cleaning up backups older than $RETENTION_DAYS days..."
DELETED_COUNT=0
while IFS= read -r old_backup; do
    if rm -f "$old_backup" 2>> "$BACKUP_LOG"; then
        DELETED_COUNT=$((DELETED_COUNT + 1))
        log "Deleted old backup: $(basename "$old_backup")"
    fi
done < <(find "$BACKUP_DIR" -maxdepth 1 -name "modelryx_backup_*.sql.gz" -type f -mtime +"$RETENTION_DAYS")

if [ $DELETED_COUNT -gt 0 ]; then
    log "Cleaned up $DELETED_COUNT old backup file(s)"
fi

# Generate summary
log ""
log "Backup Summary:"
log "  Status: SUCCESS"
log "  File: $(basename "$BACKUP_FILE")"
log "  Size: $BACKUP_SIZE"
log "  Location: $BACKUP_FILE"
log "  Timestamp: $TIMESTAMP"
log "  Retention: $RETENTION_DAYS days"
log ""

log_success "Backup process completed successfully"

# Cleanup password variable
unset PGPASSWORD
