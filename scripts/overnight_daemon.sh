#!/usr/bin/env bash
# Durable overnight runner — single bash process, survives IDE disconnect.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON=".venv/bin/python -u"
LOG="books/processed/overnight_pipeline.log"
PIDFILE="books/processed/overnight.pid"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG"; }

FROM_STEP="${1:-rag}"
log "=== DAEMON START from-step=$FROM_STEP ==="

exec "$PYTHON" scripts/run_overnight_pipeline.py --from-step "$FROM_STEP" 2>&1 | tee -a "$LOG"
