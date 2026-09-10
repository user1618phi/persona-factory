#!/usr/bin/env python3
"""Step 2 — Convert PDFs in books/raw/ to Markdown in books/processed/ via Marker."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GLOG_minloglevel", "2")

from scripts.lib.book_categories import slugify_book_name  # noqa: E402
from scripts.lib.book_exclusions import exclusion_reason, is_excluded_book  # noqa: E402
from scripts.lib.marker_cleanup import clean_marker_markdown  # noqa: E402
from scripts.lib.paths import BOOKS_PROCESSED, BOOKS_RAW  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MARKER_SINGLE = ROOT / ".venv" / "bin" / "marker_single"


def _convert_with_marker_api(pdf_path: Path, output_md: Path) -> None:
    from marker.config.parser import ConfigParser
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    temp_dir = output_md.parent / f".marker_tmp_{slugify_book_name(pdf_path)}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    config_parser = ConfigParser(
        {
            "output_dir": str(temp_dir),
            "output_format": "markdown",
            "disable_image_extraction": True,
        }
    )
    models = create_model_dict()
    converter = PdfConverter(
        config=config_parser.generate_config_dict(),
        artifact_dict=models,
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=config_parser.get_llm_service(),
    )
    rendered = converter(str(pdf_path))
    markdown, _, _ = text_from_rendered(rendered)
    markdown = clean_marker_markdown(markdown)
    output_md.write_text(markdown, encoding="utf-8")

    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)


def _convert_with_marker_cli(pdf_path: Path, output_md: Path) -> None:
    if not MARKER_SINGLE.exists():
        raise FileNotFoundError(f"marker_single not found at {MARKER_SINGLE}")

    temp_dir = output_md.parent / f".marker_tmp_{slugify_book_name(pdf_path)}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    command = [
        str(MARKER_SINGLE),
        str(pdf_path),
        "--output_dir",
        str(temp_dir),
        "--output_format",
        "markdown",
        "--disable_image_extraction",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"marker_single failed for {pdf_path.name}: {result.stderr or result.stdout}"
        )

    generated = sorted(temp_dir.rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
    if not generated:
        raise RuntimeError(f"marker_single produced no markdown for {pdf_path.name}")

    markdown = clean_marker_markdown(generated[0].read_text(encoding="utf-8"))
    output_md.write_text(markdown, encoding="utf-8")
    shutil.rmtree(temp_dir, ignore_errors=True)


def convert_pdf_file(pdf_path: Path, *, force: bool = False) -> dict:
    output_md = BOOKS_PROCESSED / f"{slugify_book_name(pdf_path)}.md"
    if is_excluded_book(pdf_path):
        return {
            "source": pdf_path.name,
            "output": output_md.name,
            "status": "excluded",
            "reason": exclusion_reason(pdf_path),
        }
    if output_md.exists() and not force:
        return {
            "source": pdf_path.name,
            "output": output_md.name,
            "status": "skipped",
            "reason": "already_exists",
        }

    errors: list[str] = []
    for strategy_name, strategy in (
        ("marker_api", _convert_with_marker_api),
        ("marker_cli", _convert_with_marker_cli),
    ):
        try:
            logger.info("Converting %s via %s", pdf_path.name, strategy_name)
            strategy(pdf_path, output_md)
            return {
                "source": pdf_path.name,
                "output": output_md.name,
                "status": "ok",
                "strategy": strategy_name,
                "bytes": output_md.stat().st_size,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s failed for %s: %s", strategy_name, pdf_path.name, exc)
            errors.append(f"{strategy_name}: {exc}")
            if output_md.exists():
                output_md.unlink()

    return {
        "source": pdf_path.name,
        "output": output_md.name,
        "status": "error",
        "errors": errors,
    }


def convert_all_pdfs(*, force: bool = False) -> dict:
    BOOKS_PROCESSED.mkdir(parents=True, exist_ok=True)
    pdf_files = sorted(BOOKS_RAW.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No PDF files found in {BOOKS_RAW}")

    results = [convert_pdf_file(path, force=force) for path in pdf_files]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(BOOKS_RAW),
        "output_dir": str(BOOKS_PROCESSED),
        "results": results,
        "ok": sum(1 for item in results if item["status"] == "ok"),
        "skipped": sum(1 for item in results if item["status"] == "skipped"),
        "excluded": sum(1 for item in results if item["status"] == "excluded"),
        "errors": sum(1 for item in results if item["status"] == "error"),
    }

    report_path = BOOKS_PROCESSED / "conversion_report.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert books/raw PDFs to Markdown via Marker.")
    parser.add_argument("--force", action="store_true", help="Reconvert even if .md exists")
    parser.add_argument("--file", type=Path, default=None, help="Convert a single PDF file")
    args = parser.parse_args()

    if args.file:
        result = convert_pdf_file(args.file, force=args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] == "error":
            raise SystemExit(1)
        return

    report = convert_all_pdfs(force=args.force)
    print("PDF conversion complete.")
    print(f"  ok: {report['ok']}, skipped: {report['skipped']}, excluded: {report['excluded']}, errors: {report['errors']}")
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
