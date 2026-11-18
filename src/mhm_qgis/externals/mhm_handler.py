"""Resolve and run an external mHM executable."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import site
import subprocess
import sysconfig
from pathlib import Path

from ..core.handlers.store.paths import version_key
from ..dependency_bootstrap import (
    python_environment,
    python_executable,
    user_scripts_path,
)

V6_ENVIRONMENT_VARIABLE = "MHM_QGIS_MHMV6"
_MRM_OUTPUT_GROUP = re.compile(
    r"^(\s*&\s*)NLoutputResults(?:_mrm)?\b", re.IGNORECASE | re.MULTILINE
)


def _require_executable(path, version_pattern: str, version_label: str) -> Path:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"mHM executable is missing: {path}")
    if os.name != "nt" and not os.access(path, os.X_OK):
        raise PermissionError(f"mHM executable is not executable: {path}")
    try:
        result = subprocess.run(  # nosec B603: a checked, existing executable file; fixed arguments, no shell
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=python_environment(),  # pip's `mhm` is a script run by QGIS's Python
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
    candidates = [str(Path(user_scripts_path()) / name), shutil.which("mhm")]
    scripts = sysconfig.get_path("scripts")
    if scripts:
        candidates.append(str(Path(scripts) / name))
    candidates.append(
        str(Path(site.getuserbase()) / ("Scripts" if os.name == "nt" else "bin") / name)
    )
    problems = []
    for candidate in dict.fromkeys(filter(None, candidates)):
        if not Path(candidate).is_file():
            continue
        try:
            return _require_executable(candidate, r"\b5\.13\.\d+\b", "5.13")
        except (OSError, ValueError) as error:  # e.g. an old build on PATH missing its libraries
            problems.append(str(error))
    raise FileNotFoundError(
        f"mhm 5.13 is not installed for {python_executable()}. "
        "Install it with: python -m pip install 'mhm>=5.13.4,<5.14'"
        + "".join(f" Skipped: {problem}" for problem in problems)
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


def mrm_text(text, runnable) -> str:
    """mRM's output group named for mHM v5 (`NLoutputResults`) or for the editor's schema."""
    return _MRM_OUTPUT_GROUP.sub(r"\1NLoutputResults" + ("" if runnable else "_mrm"), text, count=1)


def name_mrm_group(folder, *, runnable) -> None:
    path = Path(folder) / "mrm_outputs.nml"
    if path.is_file():
        old = path.read_text(encoding="utf-8")
        if (new := mrm_text(old, runnable)) != old:
            _replace_text(path, new)


def _replace_text(path, text) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(version: str, cwd, run_command, on_finished=None, v6_path=None, namelist=None, parameter=None) -> bool:
    """Launch the selected executable through the caller's terminal runner.

    `namelist` is passed as `-n <file>`: a v6 combined run reads `combine.nml`;
    `parameter` as `-p <file>`, a parameter namelist other than the setup's.
    """
    path = executable(version, v6_path=v6_path)
    options = {"cwd": str(cwd), "show": True}
    if on_finished is not None:
        options["on_finished"] = on_finished
    arguments = [str(path), *(["-n", str(namelist)] if namelist else []),
                 *(["-p", str(parameter)] if parameter else [])]
    command = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
    return bool(run_command(command, **options))


__all__ = ["executable", "mrm_text", "name_mrm_group", "run"]
