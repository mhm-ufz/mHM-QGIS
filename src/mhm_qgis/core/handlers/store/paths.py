"""Where things live: pure path construction, no I/O and no version branching.

Deliberately the leaf of the store. `core/handlers/state` imports only this, so
the arrow between state and store never reverses -- a version-aware path helper
here would have to read the input state, and the state modules need
`workspace_folder` to find that file in the first place.
"""
from __future__ import annotations

import os
from pathlib import Path

#: The plugin owns exactly one directory inside a user's project.
WORKSPACE_FOLDER_NAME = "mhm-qgis"


def plugin_root() -> str:
    """Return the plugin package root.

    This module sits three levels inside the package, so the root is three
    directories up -- not this file's own directory.
    """
    here = os.path.dirname(os.path.abspath(__file__))       # .../core/handlers/store
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def settings_path(project_folder) -> str:
    """Return editable settings in the selected setup root."""
    return os.path.join(str(project_folder), "settings.yaml")


def workspace_folder(project_folder) -> str:
    """Return the domain folder that owns every plugin file of one setup."""
    return str(project_folder)


def domain_folder(project_folder, name) -> Path:
    """Resolve a safe, non-symlink domain name without creating anything."""
    name = str(name).strip()
    reserved = {"data", "output", "restart", "<new>"}
    if (not name or name in {".", ".."} or name.casefold() in reserved
            or name.startswith(".") or any(c in name for c in '/\\\0<>:"|?*')
            or name.endswith((".", " "))):
        raise ValueError("Enter a domain folder name without path separators or reserved characters.")
    path = Path(project_folder) / WORKSPACE_FOLDER_NAME / name
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError("The domain must be a real directory, not a file or symlink.")
    return path


def version_key(version_text: str | None) -> str:
    """Return the normalized version folder name used by templates."""
    text = (version_text or "").strip().lower().removeprefix("v")
    if text.startswith("5"):
        return "v5.13"
    return "v6"


def schemas_folder(version_text: str | None) -> Path:
    """Return the packaged nml-tools schema folder for an mHM version."""
    return Path(plugin_root()) / "nml-schemas" / version_key(version_text)


def temp_folder(project_folder) -> str:
    """Return the hidden folder for temporary artifacts and plugin state."""
    return os.path.join(workspace_folder(project_folder), ".temp")


def temp_morphology_folder(project_folder) -> str:
    """Return the temporary morphology workspace, which also stages morph and LAI."""
    return os.path.join(temp_folder(project_folder), "Morphology")


def morph_staging_folder(project_folder) -> str:
    """Return the staging folder for land cover, soil, and geology outputs.

    Execute All stages here before its final publication to data.
    """
    return os.path.join(temp_morphology_folder(project_folder), "morph")


def lai_staging_folder(project_folder) -> str:
    """Return the staging folder for common-grid LAI."""
    return os.path.join(temp_morphology_folder(project_folder), "lai")


def lai_dem_staging_path(project_folder) -> str:
    """Return the staged LAI resampled onto the filled DEM grid."""
    return os.path.join(lai_staging_folder(project_folder), "lai_dem.nc")


def data_folder(project_folder) -> str:
    """Return the mHM data folder."""
    return os.path.join(workspace_folder(project_folder), "data")


def static_folder(project_folder) -> str:
    """Return the shared static data folder."""
    return os.path.join(data_folder(project_folder), "static")


def meteo_folder(project_folder) -> str:
    """Return the meteorology forcing data folder."""
    return os.path.join(data_folder(project_folder), "meteo")


def output_folder(project_folder) -> str:
    """Return the mHM output folder."""
    return os.path.join(workspace_folder(project_folder), "output")


def restart_folder(project_folder) -> str:
    """Return the mHM restart folder."""
    return os.path.join(workspace_folder(project_folder), "restart")


def portable_path(project_folder, path) -> str:
    """Return `path` relative to the project when it lies inside it, else as is."""
    if not path or not project_folder:
        return str(path or "")
    try:
        relative = os.path.relpath(str(path), str(project_folder))
    except ValueError:  # Another Windows drive.
        return str(path)
    if relative == ".." or relative.startswith(f"..{os.sep}"):
        return str(path)
    return relative.replace("\\", "/")


def project_path(project_folder, path) -> str:
    """Resolve a path saved by `portable_path` against the project folder."""
    if not path or os.path.isabs(path):
        return str(path or "")
    return os.path.abspath(os.path.join(str(project_folder), path))
