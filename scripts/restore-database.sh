#!/bin/bash
# Modelryx Database Restore Script
# Restores a previously backed-up PostgreSQL database
#
# Usage: ./restore-database.sh <backup_file> [database_name]
# Example: ./restore-database.sh modelryx_backup_20231115_120000.sql.gz modelryx
#
# Environment variables:
#   POSTGRES_HOST       - Database host (default: localhost)
#   POSTGRES_PORT       - Database port (default: 5432)
#   POSTGRES_USER       - Database user (default: postgres)
#   POSTGRES_PASSWORD   - Database password (required)

set -euo pipefail

# Configuration
BACKUP_FILE="${1:?Backup file is required}"
DB_NAME="${2:-modelryx}"
RESTORE_LOG="/tmp/restore_${DB_NAME}_$(date '+%Y%m%d_%H%M%S').log"

# Database configuration
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$RESTORE_LOG"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} [$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$RESTORE_LOG" >&2
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} [$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$RESTORE_LOG"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} [$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$RESTORE_LOG"
}

# Validation
if [ ! -f "$BACKUP_FILE" ]; then
    log_error "Backup file not found: $BACKUP_FILE"
    exit 1
fi

if [ -z "${POSTGRES_PASSWORD:-}" ]; then
    log_error "POSTGRES_PASSWORD environment variable is required"
    exit 1
fi

# Show warning about destructive operation
echo -e "${YELLOW}"
echo "╔════════════════════════════════════════════════════════════╗"
echo "║              ⚠️  DATABASE RESTORE WARNING ⚠️               ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo "This will RESTORE database: $DB_NAME"
echo "From backup file: $BACKUP_FILE"
echo ""
echo "⚠️  WARNING: This operation will:"
echo "   • Drop existing database (if it exists)"
echo "   • Restore all data from backup"
echo "   • Overwrite current production data"
echo ""
read -p "Type 'yes' to confirm restore: " -r confirmation
echo ""

if [ "$confirmation" != "yes" ]; then
    log "Restore cancelled by user"
    exit 0
fi

# Start restore
log "Starting database restore..."
log "Host: $POSTGRES_HOST:$POSTGRES_PORT"
log "Database: $DB_NAME"
log "Backup file: $BACKUP_FILE"
log "Restore log: $RESTORE_LOG"

export PGPASSWORD="$POSTGRES_PASSWORD"

# Create database (drop if exists)
log "Preparing database..."
if psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
    -c "DROP DATABASE IF EXISTS \"$DB_NAME\" WITH (FORCE);" 2>> "$RESTORE_LOG"; then
    log "Dropped existing database"
else
    log_warning "Could not drop existing database (may not exist)"
fi

if psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
    -c "CREATE DATABASE \"$DB_NAME\";" 2>> "$RESTORE_LOG"; then
    log "Created new database"
else
    log_error "Failed to create database"
    exit 1
fi

# Determine if backup is compressed
if [[ "$BACKUP_FILE" == *.gz ]]; then
    log "Restoring from compressed backup..."
    if gunzip -c "$BACKUP_FILE" | psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" \
        -U "$POSTGRES_USER" -d "$DB_NAME" --no-password 2>> "$RESTORE_LOG"; then
        log_success "Database restore completed successfully"
    else
        log_error "Database restore failed. See $RESTORE_LOG for details"
        exit 1
    fi
else
    log "Restoring from uncompressed backup..."
    if psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
        -d "$DB_NAME" -f "$BACKUP_FILE" --no-password 2>> "$RESTORE_LOG"; then
        log_success "Database restore completed successfully"
    else
        log_error "Database restore failed. See $RESTORE_LOG for details"
        exit 1
    fi
fi

# Verify restore
log "Verifying restore..."
TABLE_COUNT=$(psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
    -d "$DB_NAME" -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" \
    2>> "$RESTORE_LOG" | tr -d ' ')

if [ "$TABLE_COUNT" -gt 0 ]; then
    log_success "Restore verification successful"
    log "Database contains $TABLE_COUNT tables"
else
    log_warning "Database contains no tables - restore may be incomplete"
fi

# Generate summary
log ""
log "Restore Summary:"
log "  Status: SUCCESS"
log "  Database: $DB_NAME"
log "  Source: $(basename "$BACKUP_FILE")"
log "  Tables: $TABLE_COUNT"
log "  Restore log: $RESTORE_LOG"
log ""

log_success "Restore process completed"

# Cleanup
unset PGPASSWORD

echo -e "${GREEN}✓ Database restore completed successfully${NC}"
echo "  Next steps:"
echo "  1. Verify application connectivity: curl http://localhost:8000/health"
echo "  2. Check application logs for any errors"
echo "  3. Test critical functionality"
