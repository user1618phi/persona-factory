#!/usr/bin/env python3
"""Process newly converted Markdown books through profile, RAG, and seed steps."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def run(script: str, extra: list[str] | None = None) -> None:
    command = [PYTHON, str(ROOT / "scripts" / script), *(extra or [])]
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise SystemExit(f"Failed: {script}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Process pending converted books.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sleep", type=float, default=2.0, help="Pause between Cohere chunk calls")
    args = parser.parse_args()
    flags = ["--force"] if args.force else []
    processor_flags = [*flags, "--sleep", str(args.sleep)]

    run("book_processor.py", processor_flags)
    run("md_chunker.py", flags)
    run("seed_book_profiles.py", flags)
    print("Pending books processed.")


if __name__ == "__main__":
    main()
