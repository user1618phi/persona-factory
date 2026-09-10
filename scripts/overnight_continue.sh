#!/usr/bin/env bash
# Waits for orphan md_chunker, finishes RAG, then runs remaining pipeline steps.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG="books/processed/overnight_continue.log"
PY=".venv/bin/python -u"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG"; }

log "=== CONTINUE WATCHER START ==="

while pgrep -f "scripts/md_chunker.py" >/dev/null 2>&1; do
  log "Waiting for md_chunker to finish..."
  sleep 30
done

log "md_chunker idle — running launch_read_stamped RAG"
$PY scripts/md_chunker.py --force --file books/processed/launch_read_stamped.md 2>&1 | tee -a "$LOG"

echo '{"step":"seed","updated_at":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' > books/processed/overnight_state.json

log "Launching daemon from seed → convert → new books"
exec scripts/overnight_daemon.sh seed
