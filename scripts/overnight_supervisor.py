#!/usr/bin/env python3
"""Supervisor — runs overnight pipeline to completion, restarts on orphan steps."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "books/processed/supervisor.log"
STATE = ROOT / "books/processed/overnight_state.json"
PYTHON = sys.executable
POLL = 30


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_step() -> str:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8")).get("step", "rag")
        except json.JSONDecodeError:
            pass
    return "rag"


def is_running(pattern: str) -> bool:
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def run_pipeline(from_step: str) -> int:
    log(f"Starting pipeline from step={from_step}")
    proc = subprocess.Popen(
        [PYTHON, "-u", str(ROOT / "scripts/run_overnight_pipeline.py"), "--from-step", from_step],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log(f"[pipeline] {line.rstrip()}")
    proc.wait()
    return proc.returncode or 0


def main() -> None:
    pidfile = ROOT / "books/processed/supervisor.pid"
    pidfile.write_text(str(__import__("os").getpid()), encoding="utf-8")
    log(f"=== SUPERVISOR START pid={pidfile.read_text().strip()} ===")
    while True:
        step = load_step()
        if step == "done":
            summary = ROOT / "books/processed/overnight_summary.json"
            if summary.exists():
                log("Pipeline complete.")
                return
            log("State is done but no summary — running summary step")
            run_pipeline("new_books")
            continue

        if is_running("run_overnight_pipeline.py"):
            log("Pipeline process active — waiting")
            time.sleep(POLL)
            continue

        if is_running("md_chunker.py") or is_running("book_processor.py"):
            log("Child worker active — waiting")
            time.sleep(POLL)
            continue

        if is_running("convert_pdf.py") or is_running("marker_single"):
            log("PDF conversion active — waiting")
            time.sleep(POLL)
            continue

        log(f"No workers — launching from step={step}")
        code = run_pipeline(step)
        if code == 42:
            log("Rate limit — sleeping 15 min before retry")
            time.sleep(900)
            continue
        if code != 0:
            log(f"Pipeline exit {code} — retry in 60s")
            time.sleep(60)
            continue

        if load_step() == "done":
            log("=== SUPERVISOR FINISHED ===")
            pidfile = ROOT / "books/processed/supervisor.pid"
            if pidfile.exists():
                pidfile.unlink()
            return

        time.sleep(POLL)


if __name__ == "__main__":
    main()
