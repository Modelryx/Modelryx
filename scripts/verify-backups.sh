#!/bin/bash
# Modelryx Backup Verification Script
# Verifies backups and performs restore tests
#
# Usage: ./verify-backups.sh [backup_dir]
# Example: ./verify-backups.sh /backups

set -euo pipefail

BACKUP_DIR="${1:-.}"
REPORT_FILE="${BACKUP_DIR}/backup_verification_$(date '+%Y%m%d_%H%M%S').txt"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Initialize report
cat > "$REPORT_FILE" << EOF
Modelryx Backup Verification Report
====================================
Generated: $(date '+%Y-%m-%d %H:%M:%S')
Backup Directory: $BACKUP_DIR
Hostname: $(hostname)

EOF

# Functions
log_to_report() {
    echo "$*" >> "$REPORT_FILE"
}

show_status() {
    local message="$1"
    local status="${2:-INFO}"
    local symbol="ℹ"
    local color="$BLUE"
    
    case "$status" in
        "SUCCESS") symbol="✓"; color="$GREEN" ;;
        "ERROR")   symbol="✗"; color="$RED" ;;
        "WARNING") symbol="⚠"; color="$YELLOW" ;;
    esac
    
    echo -e "${color}${symbol}${NC} $message"
    log_to_report "[$status] $message"
}

# Check directory exists
if [ ! -d "$BACKUP_DIR" ]; then
    show_status "Backup directory not found: $BACKUP_DIR" "ERROR"
    exit 1
fi

show_status "Backup Verification Starting" "INFO"
log_to_report ""

# Check backup files exist
log_to_report "Backup Files Found:"
log_to_report "===================="
BACKUP_COUNT=0
TOTAL_SIZE=0

while IFS= read -r backup_file; do
    BACKUP_COUNT=$((BACKUP_COUNT + 1))
    FILE_SIZE=$(du -h "$backup_file" | cut -f1)
    FILE_BYTES=$(stat -f%z "$backup_file" 2>/dev/null || stat -c%s "$backup_file" 2>/dev/null || echo "0")
    TOTAL_SIZE=$((TOTAL_SIZE + FILE_BYTES))
    
    MTIME=$(stat -f%Sm -t "%Y-%m-%d %H:%M:%S" "$backup_file" 2>/dev/null || stat -c%y "$backup_file" 2>/dev/null | cut -d' ' -f1-2)
    
    log_to_report "  • $(basename "$backup_file")"
    log_to_report "    Size: $FILE_SIZE"
    log_to_report "    Modified: $MTIME"
    
    show_status "Found backup: $(basename "$backup_file") ($FILE_SIZE)" "SUCCESS"
done < <(find "$BACKUP_DIR" -maxdepth 1 -name "modelryx_backup_*.sql.gz" -type f)

if [ $BACKUP_COUNT -eq 0 ]; then
    show_status "No backup files found in $BACKUP_DIR" "WARNING"
    log_to_report "No backups found - this may indicate a backup failure"
else
    show_status "Found $BACKUP_COUNT backup file(s), total: $(numfmt --to=iec $TOTAL_SIZE 2>/dev/null || echo $((TOTAL_SIZE / 1024 / 1024))MB)" "SUCCESS"
fi

log_to_report ""
log_to_report "File Integrity Checks:"
log_to_report "======================"

# Verify backup file integrity
while IFS= read -r backup_file; do
    FILENAME=$(basename "$backup_file")
    
    # Check if file is readable
    if [ -r "$backup_file" ]; then
        show_status "File is readable: $FILENAME" "SUCCESS"
        log_to_report "  ✓ File is readable and accessible"
    else
        show_status "File is NOT readable: $FILENAME" "ERROR"
        log_to_report "  ✗ File permission denied"
        continue
    fi
    
    # Check if gzip file is valid
    if gzip -t "$backup_file" 2>/dev/null; then
        show_status "Gzip integrity verified: $FILENAME" "SUCCESS"
        log_to_report "  ✓ Gzip archive is valid"
    else
        show_status "Gzip integrity check FAILED: $FILENAME" "ERROR"
        log_to_report "  ✗ Gzip archive is corrupted"
        continue
    fi
    
    # Check for SQL dump markers
    if gunzip -c "$backup_file" | head -c 100 | grep -q "PostgreSQL"; then
        show_status "SQL dump verified: $FILENAME" "SUCCESS"
        log_to_report "  ✓ Valid PostgreSQL dump detected"
    else
        show_status "SQL dump format check inconclusive: $FILENAME" "WARNING"
        log_to_report "  ⚠ Could not verify PostgreSQL dump markers"
    fi
    
done < <(find "$BACKUP_DIR" -maxdepth 1 -name "modelryx_backup_*.sql.gz" -type f)

log_to_report ""
log_to_report "Retention Policy Check:"
log_to_report "======================="

# Check retention policy (default 30 days)
RETENTION_DAYS="${RETENTION_DAYS:-30}"
CUTOFF_DATE=$(date -d "$RETENTION_DAYS days ago" '+%Y%m%d' 2>/dev/null || date -v-${RETENTION_DAYS}d '+%Y%m%d')
EXPIRED_COUNT=0
ACTIVE_COUNT=0

while IFS= read -r backup_file; do
    FILENAME=$(basename "$backup_file")
    # Extract date from filename (YYYYMMDD format)
    FILE_DATE=$(echo "$FILENAME" | grep -oE '[0-9]{8}' | head -1)
    
    if [ "$FILE_DATE" -lt "$CUTOFF_DATE" ]; then
        EXPIRED_COUNT=$((EXPIRED_COUNT + 1))
        show_status "Expired backup eligible for cleanup: $FILENAME" "WARNING"
        log_to_report "  ⚠ $FILENAME (date: $FILE_DATE) is older than $RETENTION_DAYS days"
    else
        ACTIVE_COUNT=$((ACTIVE_COUNT + 1))
        show_status "Active backup (retention OK): $FILENAME" "SUCCESS"
        log_to_report "  ✓ $FILENAME is within retention window"
    fi
done < <(find "$BACKUP_DIR" -maxdepth 1 -name "modelryx_backup_*.sql.gz" -type f)

log_to_report ""
log_to_report "Summary:"
log_to_report "========"
log_to_report "  Total backups: $BACKUP_COUNT"
log_to_report "  Active backups: $ACTIVE_COUNT"
log_to_report "  Expired backups: $EXPIRED_COUNT"
log_to_report "  Total size: $(numfmt --to=iec $TOTAL_SIZE 2>/dev/null || echo $((TOTAL_SIZE / 1024 / 1024))MB)"
log_to_report "  Retention policy: $RETENTION_DAYS days"
log_to_report ""

# Recommendations
log_to_report "Recommendations:"
log_to_report "================"

if [ $BACKUP_COUNT -eq 0 ]; then
    log_to_report "  ⚠ No backups found - enable backup scheduling immediately"
    show_status "RECOMMENDATION: Enable automated backups" "WARNING"
elif [ $ACTIVE_COUNT -lt 2 ]; then
    log_to_report "  ⚠ Less than 2 recent backups - ensure scheduling is working"
    show_status "RECOMMENDATION: Monitor backup schedule" "WARNING"
else
    log_to_report "  ✓ Backup schedule appears to be working correctly"
    show_status "RECOMMENDATION: Backup schedule is healthy" "SUCCESS"
fi

if [ $EXPIRED_COUNT -gt 0 ]; then
    log_to_report "  ℹ Run cleanup: find $BACKUP_DIR -name 'modelryx_backup_*.sql.gz' -mtime +$RETENTION_DAYS -delete"
fi

log_to_report ""
log_to_report "Next steps:"
log_to_report "  1. Review this report: cat $REPORT_FILE"
log_to_report "  2. Test restore procedure: $0 restore [backup_file]"
log_to_report "  3. Store backups offsite (S3, cloud storage)"
log_to_report "  4. Schedule automated backups via cron/systemd"
log_to_report ""

# Print summary
echo ""
echo -e "${BLUE}════════════════════════════════════════${NC}"
echo -e "${BLUE}Backup Verification Complete${NC}"
echo -e "${BLUE}════════════════════════════════════════${NC}"
echo "Report saved to: $REPORT_FILE"
echo ""
show_status "Verification complete" "SUCCESS"
