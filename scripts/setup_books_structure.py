#!/usr/bin/env python3
"""Step 1 — Ensure books/ directory structure and move stray PDFs into raw/."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.paths import BOOKS_PROCESSED, BOOKS_PROFILES, BOOKS_RAW, BOOKS_ROOT  # noqa: E402


def setup_books_structure(*, move_root_pdfs: bool = True) -> dict:
    BOOKS_RAW.mkdir(parents=True, exist_ok=True)
    BOOKS_PROCESSED.mkdir(parents=True, exist_ok=True)
    BOOKS_PROFILES.mkdir(parents=True, exist_ok=True)

    moved: list[str] = []
    if move_root_pdfs:
        for path in sorted(BOOKS_ROOT.glob("*.pdf")):
            target = BOOKS_RAW / path.name
            if target.exists():
                continue
            shutil.move(str(path), str(target))
            moved.append(path.name)

        for ext in ("*.epub", "*.PDF"):
            for path in sorted(BOOKS_ROOT.glob(ext)):
                target = BOOKS_RAW / path.name
                if target.exists():
                    continue
                shutil.move(str(path), str(target))
                moved.append(path.name)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "books_root": str(BOOKS_ROOT),
        "raw_dir": str(BOOKS_RAW),
        "processed_dir": str(BOOKS_PROCESSED),
        "profiles_dir": str(BOOKS_PROFILES),
        "moved_files": moved,
        "raw_pdf_count": len(list(BOOKS_RAW.glob("*.pdf"))),
        "processed_md_count": len(list(BOOKS_PROCESSED.glob("*.md"))),
        "profile_json_count": len(list(BOOKS_PROFILES.glob("*.json"))),
    }

    report_path = BOOKS_ROOT / "structure_report.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup books directory structure.")
    parser.add_argument(
        "--no-move",
        action="store_true",
        help="Do not move PDF/EPUB files from books/ root into books/raw/",
    )
    args = parser.parse_args()

    report = setup_books_structure(move_root_pdfs=not args.no_move)
    print("Books structure ready.")
    print(f"  raw PDFs: {report['raw_pdf_count']}")
    print(f"  processed MD: {report['processed_md_count']}")
    print(f"  profiles JSON: {report['profile_json_count']}")
    if report["moved_files"]:
        print(f"  moved: {', '.join(report['moved_files'])}")


if __name__ == "__main__":
    main()
