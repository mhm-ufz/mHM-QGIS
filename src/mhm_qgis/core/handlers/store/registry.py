"""What a project has actually produced, and where to find it.

The question "is this output ready?" was answered four different ways: a bare
`os.path.exists` at ~150 call sites, a path-keyed JSON journal, a fingerprint
cache, and a handful of pure resolvers. This module is the one answer.

The filesystem stays authoritative -- the journal records what was produced and
when, but a file that has since been deleted is not available no matter what the
journal says. That ordering is deliberate: a user who clears an output folder
should see the plugin notice.
"""
from __future__ import annotations

import os
from pathlib import Path

from ...utils.time import utc_timestamp
from ..state import processing
from .paths import workspace_folder

#: Section of the shared processing state this module owns.
SECTION = "outputs"

#: `_masked` beats `_crop` beats the raw raster, for display.
DISPLAY_SUFFIXES = ("_masked", "_crop")


def key_for(project_folder, path) -> str:
    """Return the stable registry key for one output path.

    Workspace-relative where possible so a project stays portable; absolute
    otherwise. Separators are normalised because the key is persisted.
    """
    if not path:
        return ""
    try:
        if project_folder:
            return os.path.relpath(
                str(path), workspace_folder(project_folder)
            ).replace("\\", "/")
    except ValueError:
        pass
    return os.path.abspath(str(path)).replace("\\", "/")


def variants(path) -> tuple[Path, ...]:
    """Return the display variants of a raster, most processed first."""
    path = Path(path)
    if any(path.stem.endswith(suffix) for suffix in DISPLAY_SUFFIXES):
        return (path,)
    variants = (path.with_name(f"{path.stem}{suffix}{path.suffix}") for suffix in DISPLAY_SUFFIXES)
    return (*variants, path)


def preferred(path):
    """Return the most processed variant of a raster that exists.

    Crop and mask write siblings next to the raster they came from, and the
    masked one is what a user wants to look at. Falls back to the original.
    """
    for candidate in variants(path):
        if candidate.is_file():
            return str(candidate)
    return str(path) if path else path


def first_existing(candidates):
    """Return the first candidate that is a file, or None."""
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def outputs_in(folder, suffix: str = ".nc") -> list[Path]:
    """Return the matching files in one folder, newest first."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"*{suffix}"), key=lambda p: -p.stat().st_mtime)


def register(project_folder, path, *, name=None, loaded=False, algorithm=None,
             category=None):
    """Record that an output has been produced.

    Silently does nothing for a path that is not on disk: the journal describes
    what exists, so recording an absent file would make it lie.
    """
    if not path or not os.path.exists(str(path)):
        return None
    key = key_for(project_folder, path)
    if not key:
        return None
    entry = {
        "path": key,
        "absolute_path": os.path.abspath(str(path)),
        "exists": True,
        "loaded": bool(loaded),
        "updated_at": utc_timestamp(),
    }
    if name:
        entry["name"] = name
    if algorithm:
        entry["algorithm"] = algorithm
    if category:
        entry["category"] = category

    return processing.set_entry(
        project_folder, SECTION, key, entry, merge=True)


def available(project_folder, path) -> bool:
    """Return whether an output is on disk, keeping the journal honest.

    The filesystem decides. The journal is corrected either way, so a file that
    appeared outside the plugin gets adopted and one that vanished is marked
    gone.
    """
    if not path:
        return False
    key = key_for(project_folder, path)
    outputs = registered(project_folder)

    if os.path.exists(str(path)):
        if key not in outputs or not outputs[key].get("exists"):
            register(project_folder, path, name=os.path.basename(str(path)))
        return True
    if key in outputs and outputs[key].get("exists"):
        processing.set_entry(
            project_folder, SECTION, key, {"exists": False}, merge=True)
    return False


def registered(project_folder) -> dict:
    """Return every recorded output, keyed by its registry key."""
    outputs = processing.section(project_folder, SECTION, {})
    return outputs if isinstance(outputs, dict) else {}


__all__ = [
    "SECTION",
    "available",
    "first_existing",
    "key_for",
    "outputs_in",
    "preferred",
    "register",
    "registered",
    "variants",
]
