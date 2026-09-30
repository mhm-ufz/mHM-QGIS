# -*- coding: utf-8 -*-
"""Publish staged class inputs on the pre-scanned common L0 grid."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Mapping

from ...handlers.state.nml_settings import load_settings, relative_workspace_path, save_settings
from ...handlers.store.paths import morph_staging_folder, workspace_folder
from ...handlers.store.layout import morph_folder, is_v6
from ...handlers.file.ascii.morphology import file_matches_header


RASTER_SUFFIXES = {".asc", ".nc", ".tif", ".tiff"}
# Class definitions and projection sidecars travel with their raster untouched.
CARRIED_SUFFIXES = {".txt", ".prj", ".xml", ".cpg", ".csv"}
PATH_KEYS = ("output_path", "classdefinition_path")
SECTIONS = ("land_cover", "soil", "geology")
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
        log=None,
        task=None) -> list[Path]:
    """Publish already-final common-L0 NetCDFs, converting only for v5.13.

    Each staged raster fills an equal share of `task`'s progress; an ASCII
    export whose inputs are unchanged since it was written is kept.
    """
    from ...handlers.raster.tasks import TaskShare

    master = Path(morph_folder(project_folder))
    master.mkdir(parents=True, exist_ok=True)
    published: list[Path] = []
    v6 = is_v6(project_folder)
    files = staged_files(project_folder)
    rasters = [path for path in files if path.suffix.lower() in RASTER_SUFFIXES]
    names = {} if v6 else {path: _v5_names(project_folder, path) for path in rasters}
    # An ASCII copy of a grid that a staged NetCDF also produces is stale: the
    # NetCDF is the product. Publishing both wrote every such grid twice.
    produced = {name for path, grids in names.items() if path.suffix.lower() == ".nc" for name in grids}
    for path in [path for path in rasters if path.suffix.lower() != ".nc" and names.get(path, [None])[0] in produced]:
        rasters.remove(path)
        if log:
            log(f"Skipping {path.name}: a staged NetCDF produces it.")

    for index, path in enumerate(rasters):
        if not file_matches_header(path, l0_header):
            raise ValueError(f"{path.name} does not match the common L0 grid.")
        share = TaskShare(task).part(index, len(rasters))
        outputs = (
            (_publish_netcdf(path, master / path.name),) if v6
            else _publish_v5_raster(project_folder, path, names[path], master, l0_header, share)
        )
        share.setProgress(100)
        published.extend(output for output in outputs if output not in published)
        if log:
            for output in outputs:
                log(f"Published {output.name} to {master}.")

    for path in files:
        suffix = path.suffix.lower()
        if suffix in RASTER_SUFFIXES:
            continue
        if suffix not in CARRIED_SUFFIXES:
            if log:
                log(f"Skipping unexpected staged file: {path.name}")
            continue
        target = master / path.name
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


def _publish_netcdf(source: Path, target: Path) -> Path:
    if source.suffix.lower() != ".nc":
        raise ValueError(f"Expected a common-L0 NetCDF intermediate: {source}")
    if (
        target.is_file()
        and target.stat().st_mtime_ns == source.stat().st_mtime_ns
        and target.stat().st_size == source.stat().st_size
    ):
        return target
    temporary = target.with_name(f".{target.name}.publish")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _v5_names(project_folder, source) -> list[str]:
    """Return the ASCII file v5.13 expects for each band of a staged raster."""
    from osgeo import gdal

    dataset = gdal.Open(str(source))
    if dataset is None:
        raise ValueError(f"Could not open prepared morphology input: {source}")
    bands = dataset.RasterCount
    dataset = None
    if source.stem == "lc_periods":
        scenes = load_settings(project_folder).get("land_cover", {}).get("scenes", [])
        names = [Path(scene.get("output_path", "")).name for scene in scenes]
        if len(names) != bands or any(not name for name in names):
            raise ValueError("Land-cover periods do not match lc_periods.nc.")
        return names
    if source.stem == "soil_horizon_class":
        horizons = load_settings(project_folder).get("soil", {}).get("horizons", [])
        if bands != len(horizons):
            raise ValueError("Soil horizons do not match soil_horizon_class.nc.")
        return [f"soil_class_horizon_{index:02d}.asc" for index in range(1, bands + 1)]
    if bands != 1:
        raise ValueError(f"Expected one raster layer in {source.name}.")
    return [source.with_suffix(".asc").name]


def _publish_v5_raster(project_folder, source, names, master, header, task) -> tuple[Path, ...]:
    """Convert one exact-grid staged raster into the ASCII files v5.13 expects."""
    from ...handlers.raster.tasks import write_domain_dem_ascii

    outputs = []
    for band, name in enumerate(names, 1):
        target = master / name
        write_domain_dem_ascii(
            source, target, header, None, band_index=band,
            task=task.part(band - 1, len(names)), project_folder=project_folder,
        )
        outputs.append(target)
    return tuple(outputs)


def _configured_rasters(project_folder):
    """Only model products, never the original soil horizon inputs."""
    workspace = Path(workspace_folder(project_folder))
    settings = load_settings(project_folder)
    paths = set()
    sections = list(SECTIONS)
    if settings.get("lai", {}).get("mode") == "mhm_ready":
        sections.append("lai")
    for section in sections:
        record = settings.get(section, {})
        for item in [record, *record.get("scenes", [])]:
            value = item.get("output_path")
            if value and Path(value).suffix.lower() in RASTER_SUFFIXES:
                paths.add(workspace / value)
    return sorted(paths)


def _repoint_settings(project_folder, published, log=None) -> None:
    """Rewrite the namelist handoff so every path names the published copy.

    Anything already sitting in `data/` is repointed too, not just what
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

    sections = list(SECTIONS)
    if settings.get("lai", {}).get("mode") == "mhm_ready":
        sections.append("lai")
    for section in sections:
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
        if not is_v6(project_folder) and section == "land_cover" and record.get("scenes"):
            if record.get("output_path"):
                record["output_path"] = ""
                changed = True
    if changed:
        save_settings(project_folder, settings)
        if log:
            log("nml-settings.json now points at the published model inputs.")


def _published_path(by_name, value, v6):
    if not value:
        return None
    path = Path(value)
    if not v6 and path.name == "soil_horizon_class.nc":
        return by_name.get("soil_class_horizon_01.asc")
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
    "publish_model_inputs",
    "staged_files",
]
