#!/usr/bin/env bash
# Night orchestrator — auto-continue pipeline after each step until done.
#
# Usage:
#   ./scripts/night-orchestrator.sh start     # wait for workers, then run to completion
#   ./scripts/night-orchestrator.sh continue  # same as start (resume from overnight_state.json)
#   ./scripts/night-orchestrator.sh status    # show step, PIDs, chunk counts
#   ./scripts/night-orchestrator.sh logs      # follow logs
#   ./scripts/night-orchestrator.sh stop      # stop supervisor only (workers keep running)
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY=".venv/bin/python -u"
SUPERVISOR="scripts/overnight_supervisor.py"
PIDFILE="books/processed/supervisor.pid"
STATE="books/processed/overnight_state.json"
LOG_SUP="books/processed/supervisor.log"
LOG_PIPE="books/processed/overnight_pipeline.log"

ensure_venv() {
  if [[ ! -x ".venv/bin/python" ]]; then
    echo "ERROR: .venv not found. Run: python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
  fi
}

supervisor_running() {
  if [[ -f "$PIDFILE" ]]; then
    local pid
    pid=$(cat "$PIDFILE")
    if kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  pgrep -f "overnight_supervisor.py" >/dev/null 2>&1
}

cmd_start() {
  ensure_venv
  mkdir -p books/processed
  if supervisor_running; then
    echo "Supervisor already running (see: $0 status)"
    exit 0
  fi
  local from_step="${1:-}"
  if [[ -n "$from_step" ]]; then
    echo "{\"step\":\"$from_step\",\"updated_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$STATE"
    echo "State set to step: $from_step"
  fi
  nohup $PY "$SUPERVISOR" >> books/processed/supervisor.nohup.log 2>&1 &
  local pid=$!
  echo "$pid" > "$PIDFILE"
  disown "$pid" 2>/dev/null || true
  echo "Night orchestrator started (PID $pid)"
  echo "  state:  $STATE"
  echo "  logs:   $0 logs"
  echo "  status: $0 status"
}

cmd_stop() {
  if [[ -f "$PIDFILE" ]]; then
    local pid
    pid=$(cat "$PIDFILE")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "Stopped supervisor PID $pid"
    fi
    rm -f "$PIDFILE"
  fi
  pkill -f "overnight_supervisor.py" 2>/dev/null && echo "Stopped supervisor process" || true
}

cmd_status() {
  echo "=== Night orchestrator status ==="
  if [[ -f "$STATE" ]]; then
    echo "State file:"
    cat "$STATE"
  else
    echo "State file: (missing) — will start from 'rag'"
  fi
  echo ""
  if supervisor_running; then
    echo "Supervisor: RUNNING (PID $(cat "$PIDFILE" 2>/dev/null || pgrep -f overnight_supervisor.py))"
  else
    echo "Supervisor: stopped — run: $0 start"
  fi
  echo ""
  echo "Active workers:"
  pgrep -fl "md_chunker.py|book_processor.py|run_overnight_pipeline.py|convert_pdf.py|marker_single" 2>/dev/null || echo "  (none)"
  echo ""
  if [[ -f "$LOG_SUP" ]]; then
    echo "Last supervisor lines:"
    tail -5 "$LOG_SUP"
  fi
  if [[ -f books/processed/overnight_summary.json ]]; then
    echo ""
    echo "=== COMPLETE ==="
    cat books/processed/overnight_summary.json
  fi
}

cmd_logs() {
  touch "$LOG_SUP" "$LOG_PIPE"
  tail -f "$LOG_SUP" "$LOG_PIPE"
}

case "${1:-status}" in
  start|continue|run)
    cmd_start "${2:-}"
    ;;
  stop)
    cmd_stop
    ;;
  status)
    cmd_status
    ;;
  logs|log|tail)
    cmd_logs
    ;;
  *)
    echo "Usage: $0 {start|continue|status|logs|stop} [from-step]"
    echo "  from-step: wait|profiles|rag|seed|convert|new_books"
    exit 1
    ;;
esac
