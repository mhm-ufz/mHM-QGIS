"""Path-only LAI preparation for morphology workflows."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ....externals.mhm_tools_handler import copy_lai_file_to_grid, lai_time_step
from ...grid import write_header_file
from ...handlers.state.nml_settings import relative_workspace_path, update_section
from ...handlers.store.layout import lai_folder
from ...handlers.store.paths import lai_dem_staging_path


DEFAULT_TIMESTEP = "Long Term Mean Monthly Gridded Data"


def parse_source(source: str) -> tuple[str | None, str | None]:
    """Extract a NetCDF path and variable from a QGIS/GDAL source string."""
    if not source:
        return None, None
    value = source.split("|", 1)[0].strip()
    if not value.upper().startswith("NETCDF:"):
        return os.path.normpath(value), None
    value = value[len("NETCDF:") :]
    if value.startswith(('"', "'")):
        quote = value[0]
        end = value.find(quote, 1)
        if end > 0:
            variable = value[end + 2 :] if value[end + 1 :].startswith(":") else ""
            return os.path.normpath(value[1:end]), variable.strip('"\'') or None
    if os.path.exists(value):
        return os.path.normpath(value), None
    if ":" in value:
        path, variable = value.rsplit(":", 1)
        return os.path.normpath(path), variable.strip().strip('"\'') or None
    return os.path.normpath(value), None


def task_options(
    project_folder,
    source: str,
    filled_dem: str,
    *,
    crs: str = "",
    target_timestep: str = DEFAULT_TIMESTEP,
) -> dict | None:
    """Build primitive-only arguments for the LAI resampling command."""
    source_path, source_variable = parse_source(source)
    if not source_path or not Path(source_path).is_file():
        return None
    return {
        "source_path": source_path,
        "source_variable": source_variable,
        "output_path": lai_dem_staging_path(project_folder),
        "filled_dem": str(filled_dem),
        "dem_crs": str(crs or ""),
        "target_timestep": target_timestep or DEFAULT_TIMESTEP,
        "method": "bilinear",
    }


def publish_to_l0(
    project_folder,
    l0_header: dict,
    crs: str = "",
    *,
    source: str = "",
    target_timestep: str = DEFAULT_TIMESTEP,
    classdefinition_source: str = "",
    log=None,
) -> Path:
    """Publish unmasked LAI over the complete common L0 extent."""
    folder = Path(lai_folder(project_folder))
    staged = Path(lai_dem_staging_path(project_folder))
    if not staged.is_file():
        raise FileNotFoundError("Prepare LAI on the common DEM grid first.")
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / "lai.nc"
    copy_lai_file_to_grid(
        staged,
        output,
        l0_header,
        crs or "",
        "Monthly LAI on the common L0 model extent",
        log=log,
    )
    _write_header(folder, l0_header)
    if source:
        source_path, source_variable = parse_source(source)
        settings = {
            "mode": "netcdf",
            "source_path": str(Path(source_path).resolve()) if source_path else "",
            "source_variable": str(source_variable or ""),
            "target_timestep": target_timestep or DEFAULT_TIMESTEP,
            "time_step": lai_time_step(target_timestep or DEFAULT_TIMESTEP),
            "output_path": relative_workspace_path(project_folder, output),
            "variable": "lai",
        }
        if classdefinition_source:
            definition = folder / "LAI_classdefinition.txt"
            shutil.copy2(classdefinition_source, definition)
            settings["classdefinition_path"] = relative_workspace_path(
                project_folder, definition
            )
        update_section(
            project_folder,
            "lai",
            settings,
        )
    return output


def _write_header(folder: Path, l0_header: dict) -> Path:
    path = folder / "header.txt"
    header = dict(l0_header)
    header["nodata_value"] = -9999.0
    write_header_file(path, header)
    return path


__all__ = [
    "DEFAULT_TIMESTEP",
    "parse_source",
    "publish_to_l0",
    "task_options",
]
