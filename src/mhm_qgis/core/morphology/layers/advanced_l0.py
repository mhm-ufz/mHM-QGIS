# -*- coding: utf-8 -*-
"""Publish staged class inputs on the pre-scanned common L0 grid."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Mapping

from . import CATEGORICAL_PAD_VALUE, LAI_PAD_VALUE
from ...handlers.state.nml_settings import load_settings, relative_workspace_path, save_settings
from ...handlers.store.paths import morph_staging_folder, workspace_folder
from ...handlers.store.layout import morph_folder, is_v6
from ...handlers.file.ascii.morphology import file_matches_header, pad_l0_file_to_header


# Rasters are padded onto the L0 grid; everything else is carried across as is.
RASTER_SUFFIXES = {".asc", ".nc", ".tif", ".tiff"}
# Class definitions and projection sidecars travel with their raster untouched.
CARRIED_SUFFIXES = {".txt", ".prj", ".xml", ".cpg", ".csv"}
PATH_KEYS = ("output_path", "classdefinition_path")
SECTIONS = ("land_cover", "soil", "geology")
# Staged rasters are named after their model input, so the file name is what
# tells us how to pad them: (name prefixes, pad value, integer output).
PAD_SPECS = (
    (
        ("lc", "land_cover", "land_use", "landuse", "soil", "geology"),
        CATEGORICAL_PAD_VALUE,
        True,
    ),
    (("lai",), LAI_PAD_VALUE, False),
)


def pad_spec_for(path: Path) -> tuple[float | int | None, bool]:
    """Return the pad value and integer flag for one staged raster.

    Class layers pad with a valid class and LAI with a valid leaf area index;
    padding them with nodata would leave cells mHM cannot read inside the
    model domain. Anything unrecognised keeps the historical nodata padding.
    """
    stem = path.stem.lower()
    for prefixes, pad_value, integer in PAD_SPECS:
        if stem.startswith(prefixes):
            return pad_value, integer
    return None, True


def staged_files(project_folder) -> list[Path]:
    """Return the files Execute All staged for publication, rasters first."""
    staging = Path(morph_staging_folder(project_folder))
    if not staging.is_dir():
        return []
    files = [path for path in sorted(staging.iterdir()) if path.is_file()]
    return (
        [path for path in files if path.suffix.lower() in RASTER_SUFFIXES]
        + [path for path in files if path.suffix.lower() not in RASTER_SUFFIXES]
    )


def publish_model_inputs(
        project_folder,
        l0_header: Mapping[str, Any],
        *,
        log=None) -> list[Path]:
    """Publish exact-L0 copies, retaining full source rasters for extent changes."""
    master = Path(morph_folder(project_folder))
    master.mkdir(parents=True, exist_ok=True)
    published: list[Path] = []

    # Older projects moved their sources into master. Preserve those before
    # cropping; never let a smaller domain destroy the reusable coverage.
    staging = Path(morph_staging_folder(project_folder))
    for path in _configured_rasters(project_folder):
        source = staging / path.name
        if path.is_file() and not source.exists():
            staging.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, source)

    for path in staged_files(project_folder):
        suffix = path.suffix.lower()
        target = master / path.name
        if suffix in RASTER_SUFFIXES:
            convert = suffix in {".tif", ".tiff"} or (suffix == ".nc" and not is_v6(project_folder))
            if convert:
                target = target.with_suffix(".asc")
            if (target.is_file() and target.stat().st_mtime_ns >= path.stat().st_mtime_ns
                    and file_matches_header(target, l0_header)):
                continue
            pad_value, integer = pad_spec_for(path)
            temporary = target.with_name(f".{target.stem}.publish{target.suffix}")
            try:
                if convert:
                    from ...handlers.raster.tasks import write_domain_dem_ascii
                    write_domain_dem_ascii(path, temporary, l0_header, None, fill_value=pad_value)
                else:
                    shutil.copy2(path, temporary)
                    pad_l0_file_to_header(
                        temporary, l0_header, nodata_value=-9999,
                        integer=integer, pad_value=pad_value, fill_nodata=pad_value is not None,
                    )
                if not file_matches_header(temporary, l0_header):
                    raise ValueError(f"{path.name} does not match the common L0 grid.")
                os.replace(temporary, target)
                if convert and temporary.with_suffix(".prj").is_file():
                    os.replace(temporary.with_suffix(".prj"), target.with_suffix(".prj"))
            finally:
                temporary.unlink(missing_ok=True)
            if log:
                padding = "nodata" if pad_value is None else str(pad_value)
                log(
                    f"{path.name} placed on the common L0 grid "
                    f"(padded with {padding})."
                )
        elif suffix not in CARRIED_SUFFIXES:
            if log:
                log(f"Skipping unexpected staged file: {path.name}")
            continue
        else:
            if target.is_symlink():
                target.unlink()
            shutil.copy2(path, target)
        published.append(target)
        if log:
            log(f"Published {target.name} to {master}.")

    _repoint_settings(project_folder, published, log=log)
    for path in _configured_rasters(project_folder):
        if path.is_file() and not file_matches_header(path, l0_header):
            raise ValueError(f"Published input {path} does not match the common L0 grid.")
    return published


def _configured_rasters(project_folder):
    """Only model products, never the original soil horizon inputs."""
    workspace = Path(workspace_folder(project_folder))
    settings = load_settings(project_folder)
    paths = set()
    for section in SECTIONS:
        record = settings.get(section, {})
        for item in [record, *record.get("scenes", [])]:
            value = item.get("output_path")
            if value and Path(value).suffix.lower() in RASTER_SUFFIXES:
                paths.add(workspace / value)
    return sorted(paths)


def _repoint_settings(project_folder, published, log=None) -> None:
    """Rewrite the namelist handoff so every path names the published copy.

    Anything already sitting in `data/master` is repointed too, not just what
    this run moved, so a reused stage whose output was published earlier still
    ends up with a resolvable path.
    """
    master = Path(morph_folder(project_folder))
    by_name = {path.name: path for path in published}
    if master.is_dir():
        for path in master.iterdir():
            if path.is_file():
                by_name.setdefault(path.name, path)
    settings = load_settings(project_folder)
    changed = False

    for section in SECTIONS:
        record = settings.get(section)
        if not isinstance(record, dict):
            continue
        for key in PATH_KEYS:
            value = str(record.get(key, "") or "")
            target = _published_path(by_name, value, is_v6(project_folder))
            if target is None:
                continue
            updated = relative_workspace_path(project_folder, target)
            if updated != value:
                record[key] = updated
                changed = True
        scenes = record.get("scenes")
        if isinstance(scenes, list):
            for scene in scenes:
                if not isinstance(scene, dict):
                    continue
                value = str(scene.get("output_path", "") or "")
                target = _published_path(by_name, value, is_v6(project_folder))
                if target is None:
                    continue
                updated = relative_workspace_path(project_folder, target)
                if updated != value:
                    scene["output_path"] = updated
                    changed = True

    if changed:
        save_settings(project_folder, settings)
        if log:
            log("nml-settings.json now points at the published model inputs.")


def _published_path(by_name, value, v6):
    if not value:
        return None
    path = Path(value)
    if path.suffix.lower() in {".tif", ".tiff"} or (path.suffix.lower() == ".nc" and not v6):
        return by_name.get(path.with_suffix(".asc").name)
    return by_name.get(path.name)


def missing_model_inputs(project_folder) -> list[str]:
    """Return namelist paths that do not resolve to an existing file."""
    workspace = Path(workspace_folder(project_folder))
    settings = load_settings(project_folder)
    missing: list[str] = []

    def check(value):
        text = str(value or "")
        if not text:
            return
        path = Path(text)
        if not path.is_absolute():
            path = workspace / path
        if not path.is_file():
            missing.append(text)

    for section in SECTIONS + ("lai",):
        record = settings.get(section)
        if not isinstance(record, dict):
            continue
        for key in PATH_KEYS:
            check(record.get(key))
        scenes = record.get("scenes")
        if isinstance(scenes, list):
            for scene in scenes:
                if isinstance(scene, dict):
                    check(scene.get("output_path"))
    return missing


__all__ = [
    "missing_model_inputs",
    "pad_spec_for",
    "publish_model_inputs",
    "staged_files",
]
