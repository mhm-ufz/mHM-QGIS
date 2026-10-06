"""Resolve and run an external mHM executable."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import site
import subprocess
import sys
import sysconfig
from pathlib import Path

from ..core.handlers.store.paths import version_key

V6_ENVIRONMENT_VARIABLE = "MHM_QGIS_MHMV6"


def _require_executable(path, version_pattern: str, version_label: str) -> Path:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"mHM executable is missing: {path}")
    if os.name != "nt" and not os.access(path, os.X_OK):
        raise PermissionError(f"mHM executable is not executable: {path}")
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError as error:
        raise OSError(f"Could not start mHM executable {path}: {error}") from error
    output = "\n".join(filter(None, (result.stdout.strip(), result.stderr.strip())))
    if result.returncode or not re.search(version_pattern, output):
        raise ValueError(
            f"Expected mHM {version_label}, got "
            f"{output or 'no version output'} from {path}."
        )
    return path


def _installed_v5() -> Path:
    name = "mhm.exe" if os.name == "nt" else "mhm"
    candidates = [shutil.which("mhm")]
    scripts = sysconfig.get_path("scripts")
    if scripts:
        candidates.append(str(Path(scripts) / name))
    candidates.append(
        str(Path(site.getuserbase()) / ("Scripts" if os.name == "nt" else "bin") / name)
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return _require_executable(candidate, r"\b5\.13\.4\b", "5.13.4")
    raise FileNotFoundError(
        f"mhm 5.13.4 is not installed for {sys.executable}. "
        "Install it with: python -m pip install mhm==5.13.4"
    )


def executable(version: str, v6_path=None) -> Path:
    """Return the installed v5 command or validated user-supplied v6 binary."""
    key = version_key(version)
    if key == "v5.13":
        return _installed_v5()
    selected = v6_path or os.environ.get(V6_ENVIRONMENT_VARIABLE)
    if not selected:
        raise FileNotFoundError("Select an mHM v6 executable before running mHM.")
    return _require_executable(selected, r"\b6(?:\.\d+)+\b", "v6")


def run(version: str, cwd, run_command, on_finished=None, v6_path=None, namelist=None) -> bool:
    """Launch the selected executable through the caller's terminal runner.

    `namelist` is passed as `-n <file>`: a v6 combined run reads `combine.nml`.
    """
    options = {"cwd": str(cwd), "show": True}
    if on_finished is not None:
        options["on_finished"] = on_finished
    path = executable(version, v6_path=v6_path)
    arguments = [str(path), *(["-n", str(namelist)] if namelist else [])]
    command = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
    return bool(run_command(command, **options))


__all__ = ["executable", "run"]
