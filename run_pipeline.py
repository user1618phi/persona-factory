#!/usr/bin/env python3
"""
The Persona Factory — Hybrid Books Pipeline Runner

Steps:
  1. setup_books_structure
  2. convert_pdf (Marker)
  3. book_processor (JSON profiles)
  4. md_chunker (Semantic RAG)
  Book profiles remain offline artifacts; they are never seeded into author identity.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable


def run_step(name: str, script: str, extra_args: list[str] | None = None) -> None:
    command = [PYTHON, str(ROOT / "scripts" / script), *(extra_args or [])]
    print(f"\n{'=' * 72}\nSTEP: {name}\n{'=' * 72}")
    print("Command:", " ".join(command))
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise SystemExit(f"Pipeline failed at step '{name}' (exit {result.returncode})")


def print_install_instructions() -> None:
    print(
        """
DEPENDENCIES (run once):
  python3.12 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt

MARKER (PDF -> Markdown):
  pip install marker-pdf
  # First run downloads layout models (~2-4 GB). Requires Python 3.10+ and enough disk/RAM.

SEMANTIC CHUNKER:
  pip install langchain-experimental langchain-huggingface sentence-transformers torch

SUPABASE:
  # Add to .env:
  SUPABASE_URL=https://<project>.supabase.co
  SUPABASE_SERVICE_ROLE_KEY=<service_role_key>
  GEMINI_API_KEY=<your_key>

DATABASE:
  # Run in Supabase SQL Editor:
  #   supabase/init.sql
  #   supabase/migrations/002_knowledge_metadata.sql  (if upgrading existing DB)

USAGE:
  .venv/bin/python run_pipeline.py
  .venv/bin/python run_pipeline.py --from-step convert --force
  .venv/bin/python run_pipeline.py --skip-seed --dry-run-chunker
"""
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run hybrid books pipeline end-to-end.")
    parser.add_argument(
        "--from-step",
        choices=["setup", "convert", "profiles", "chunk"],
        default="setup",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-seed", action="store_true")
    parser.add_argument("--dry-run-chunker", action="store_true")
    parser.add_argument("--show-deps", action="store_true")
    args = parser.parse_args()

    if args.show_deps:
        print_install_instructions()
        return

    steps = [
        ("setup", "setup_books_structure.py", []),
        ("convert", "convert_pdf.py", ["--force"] if args.force else []),
        ("profiles", "book_processor.py", ["--force"] if args.force else []),
        (
            "chunk",
            "md_chunker.py",
            (["--force"] if args.force else []) + (["--dry-run"] if args.dry_run_chunker else []),
        ),
    ]

    start_index = next(index for index, (name, _, _) in enumerate(steps) if name == args.from_step)
    selected = steps[start_index:]
    if args.skip_seed:
        print("--skip-seed is retained for compatibility; V2.1 has no book-profile seed step.")

    for name, script, extra in selected:
        run_step(name, script, extra)

    print("\nPipeline completed successfully.")


if __name__ == "__main__":
    main()
