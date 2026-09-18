"""Run the version-specific mHM executable shipped for development."""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from ..core.handlers.store.paths import version_key


def executable(version: str) -> Path:
    """Return the executable selected by the configured mHM version."""
    root = Path(__file__).resolve().parents[1]
    key = version_key(version)
    path = root / "vSpecific" / ("v5_13" if key == "v5.13" else "v6")
    path /= "mhmv5" if key == "v5.13" else "mhmv6"
    if not path.is_file():
        raise FileNotFoundError(f"mHM executable is missing: {path}")
    if not os.access(path, os.X_OK):
        raise PermissionError(f"mHM executable is not executable: {path}")
    return path


def run(version: str, cwd, run_command, on_finished=None) -> bool:
    """Launch the selected executable through the caller's terminal runner."""
    options = {"cwd": str(cwd), "show": True}
    if on_finished is not None:
        options["on_finished"] = on_finished
    return bool(
        run_command(shlex.quote(str(executable(version))), **options)
    )


__all__ = ["executable", "run"]
