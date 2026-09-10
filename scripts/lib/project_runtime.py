"""Runtime bootstrap shared by operational scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_project_venv(script_file: str, root: Path) -> None:
    """Re-exec an operational script through the project's Python environment."""
    project_venv = (root / ".venv").resolve()
    venv_python = project_venv / "bin" / "python"
    if Path(sys.prefix).resolve() == project_venv:
        return
    # Tests and CI import scripts with an interpreter that already has the
    # dependencies; they opt out explicitly instead of re-exec'ing into .venv.
    if os.environ.get("PERSONA_SKIP_VENV_CHECK"):
        return
    if not venv_python.exists():
        raise SystemExit(
            "Project virtual environment is missing. Run:\n"
            "  python3.12 -m venv .venv\n"
            "  .venv/bin/pip install -r requirements.txt -r requirements-dev.txt"
        )
    os.execv(
        str(venv_python),
        [str(venv_python), str(Path(script_file).resolve()), *sys.argv[1:]],
    )
