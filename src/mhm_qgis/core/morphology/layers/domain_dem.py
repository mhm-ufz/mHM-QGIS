# -*- coding: utf-8 -*-
"""Write every domain DEM on the common L0 grid.

Delineation records each domain's mask but not its DEM, because the model
extent is not known until meteorology has fixed the L2 grid. Morphology Setup
then masks the cropped L0 DEM with each mask, so every `data/<domain>/dem.asc`
has the same matrix size as the shared morphology and meteorology inputs.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Mapping

from ...handlers.file.netcdf.raster import file_path
from ...handlers.raster.tasks import write_domain_dem_ascii
from .domain_dem_nc import write_domain_dem_netcdf
from ...handlers.state.domain_state import (
    active_domain_records,
    load_state as load_domain_state,
    resolve_output_path,
)
from ...handlers.store.paths import domain_data_folder
from ...handlers.store.layout import domain_dem_path, morph_folder


# mHM reads its morphology per domain from `dir_Morpho`, so each domain needs
# the shared rasters beside its own dem.asc. Files are listed rather than
# globbed so an unrelated file in data/master never leaks into a domain, and
# they are grouped so that a group with several spellings -- v5 writes a soil
# class raster where v6 writes a horizon cube -- only counts as missing when
# none of them is there. Land cover and LAI are absent on purpose:
# the namelist points every domain at the shared copy under data/master.
DOMAIN_INPUT_GROUPS = (
    ("slope", ("slope.asc",)),
    ("aspect", ("aspect.asc",)),
    ("flow accumulation", ("facc.asc",)),
    ("flow direction", ("fdir.asc",)),
    ("gauge positions", ("idgauges.asc",)),
    ("soil class", ("soil_class.asc", "soil_horizon_class.nc")),
    (
        "soil class definition",
        ("soil_classdefinition.txt", "soil_classdefinition_iFlag_soilDB_1.txt"),
    ),
    ("geology class", ("geology_class.asc",)),
    ("geology class definition", ("geology_classdefinition.txt",)),
)
DOMAIN_INPUT_NAMES = tuple(
    name for _label, names in DOMAIN_INPUT_GROUPS for name in names
)
SIDECAR_SUFFIXES = (".prj",)


def link_master_inputs(project_folder, directory, log=None, *, strict=False) -> list[Path]:
    """Symlink the shared morphology inputs from data/master into one domain.

    Run this after `advanced_l0.publish_model_inputs`: an advanced or
    mHM-ready soil, geology, or land-cover raster only reaches data/master at
    publication, so linking earlier would silently find nothing. Every domain
    reads the same bytes, so these are links rather than copies -- the shared
    rasters run to gigabytes and a domain's own dem.asc is the only file that
    differs.
    """
    master = Path(morph_folder(project_folder))
    destination = Path(directory)
    names: list[str] = []
    absent: list[str] = []

    for label, group in DOMAIN_INPUT_GROUPS:
        present = [name for name in group if (master / name).is_file()]
        if not present:
            # Ungauged projects do not produce a gauge-position raster.
            if group != ("idgauges.asc",):
                absent.append(label)
            continue
        for name in present:
            names.append(name)
            names.extend(
                sidecar.name
                for sidecar in (
                    (master / name).with_suffix(suffix)
                    for suffix in SIDECAR_SUFFIXES
                )
                if sidecar.is_file()
            )

    if absent and strict:
        raise FileNotFoundError(f"Published domain inputs missing from {master}: " + ", ".join(absent))
    linked = _link_shared_inputs(master, destination, names, log) if names else []
    if log:
        log(
            f"Linked {len(linked)} shared morphology file(s) into "
            f"{destination}."
        )
        if absent:
            # A silent skip once left every domain with only its dem.asc, so
            # name what data/master could not supply.
            log(
                f"WARNING: {master} has no "
                + ", ".join(absent)
                + f"; {destination.name} will run without it."
            )
    return linked


def _link_shared_inputs(master: Path, destination: Path, names, log) -> list[Path]:
    """Link the named files, falling back to copies where links are refused."""
    from ....applications import mhm_tools_handler

    try:
        # mHM-tools logs two lines per pattern; the caller's summary is enough.
        return list(
            mhm_tools_handler.link_folder_tree(master, destination, tuple(names))
        )
    except OSError as error:
        # Windows refuses symlinks without Developer Mode or admin rights.
        if log:
            log(
                "WARNING: Could not symlink the shared morphology inputs, "
                f"copying them instead: {error}"
            )
    destination.mkdir(parents=True, exist_ok=True)
    return [_copy_input(master / name, destination / name) for name in names]


def _copy_input(source: Path, target: Path) -> Path:
    """Copy one shared input into place without leaving a partial file."""
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def domain_dem_plan(project_folder) -> list[dict[str, Any]]:
    """Return one entry per active domain: its mask and its target DEM."""
    state = load_domain_state(project_folder)
    plan: list[dict[str, Any]] = []
    for record in active_domain_records(state):
        if record.get("domain_id") is None:
            continue
        outlet_id = str(record.get("outlet_id", ""))
        name = "dem_extent" if record.get("is_dem_domain") else outlet_id
        plan.append({
            "domain_id": int(record["domain_id"]),
            "outlet_id": outlet_id,
            "name": name,
            "mask": _domain_mask(project_folder, record),
            "directory": domain_data_folder(project_folder, name),
            "dem_path": domain_dem_path(project_folder, name),
        })
    return plan


def _domain_mask(project_folder, record) -> str:
    """Return the delineated mask raster path for one domain."""
    if record.get("is_dem_domain"):
        from ...handlers.store.paths import geometry_folder

        return os.path.join(
            geometry_folder(project_folder),
            "Watersheds",
            "DomainDelineation",
            "4_watershed_DEM.tif",
        )
    value = record.get("mask_path")
    if not value:
        return ""
    return str(resolve_output_path(project_folder, value))


def write_domain_dems(
        project_folder,
        cropped_dem: str,
        l0_header: Mapping[str, Any],
        *,
        reference_path=None,
        log=None) -> list[dict[str, Any]]:
    """Mask the cropped L0 DEM with each domain mask and write its dem.asc."""
    plan = domain_dem_plan(project_folder)
    if not plan:
        return []
    if not file_path(cropped_dem).is_file():
        raise FileNotFoundError(
            f"The cropped L0 DEM is required before writing domain DEMs: "
            f"{cropped_dem}"
        )

    written: list[dict[str, Any]] = []
    for entry in plan:
        mask = entry["mask"]
        if not mask or not Path(mask).is_file():
            raise FileNotFoundError(
                f"Domain {entry['name']} has no delineated mask: {mask}"
            )
        os.makedirs(entry["directory"], exist_ok=True)
        if log:
            log(
                f"Writing domain {entry['name']} DEM on the common L0 grid "
                f"({int(l0_header['ncols'])} x {int(l0_header['nrows'])} cells)."
            )
        writer = (
            write_domain_dem_netcdf
            if str(entry["dem_path"]).endswith(".nc")
            else write_domain_dem_ascii
        )
        writer(
            cropped_dem,
            entry["dem_path"],
            l0_header,
            mask,
            reference_path=reference_path,
        )
        written.append(entry)
        if log:
            log(f"Domain {entry['name']} DEM written: {entry['dem_path']}")
    return written


__all__ = [
    "DOMAIN_INPUT_GROUPS",
    "DOMAIN_INPUT_NAMES",
    "link_master_inputs",
    "domain_dem_plan",
    "write_domain_dems",
]
