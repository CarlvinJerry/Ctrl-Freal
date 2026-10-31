#!/usr/bin/env bash
set -euo pipefail

cd /home/carlvinjerry/tcl-remote/v2

SOURCE="static/glass.css"
BACKUP_DIR="backups/glass"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "Usage: ./update-glass.sh path/to/new-glass.css"
    exit 1
fi

mkdir -p "$BACKUP_DIR"

cp "$SOURCE" "$BACKUP_DIR/glass-$TIMESTAMP.css"
cp "$1" "$SOURCE"

echo "Glass CSS updated successfully."
echo "Backup: $BACKUP_DIR/glass-$TIMESTAMP.css"
echo "Refresh Safari to see the changes."
