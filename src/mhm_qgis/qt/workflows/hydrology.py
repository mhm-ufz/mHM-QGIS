"""DEM derivatives and flow/channel files, prepared on demand.

Execute All runs `start_dem_derivatives` as its first stage. The Domain
Delineator, domain assignment and elevation bands need the same files before
Execute All has run: `start_domain_preflight` prepares whatever is missing, DEM
first, and `start_hydrology` rebuilds the delineator's channel preview.
"""
from __future__ import annotations

import os
from functools import partial
from pathlib import Path

from qgis.PyQt import QtCore

from ...core.executions.morphology import commands
from ...core.handlers.store.paths import temp_morphology_folder
from ...qgis_bridge import layers
from ..controllers import grid
from . import lifecycle


def path(dialog, name):
    """Return a fixed file in the domain's morphology workspace."""
    return str(Path(temp_morphology_folder(dialog.project_folder)) / name)


def filled_dem_path(dialog):
    return path(dialog, "filled_dem.nc")


def channel_network_path(dialog):
    return path(dialog, "2_channel_network.shp")


def _dem_ready(dialog):
    return all(os.path.isfile(path(dialog, name)) for name in ("filled_dem.nc", "dem_derivatives.nc"))


def _locked(dialog, controls):
    """Also lock the project selector while a task started from a form runs."""
    controls = tuple(controls)
    return (*controls, dialog.pushButton_BrowseProjectFolder) if controls else controls


def dem_options(dialog):
    """Snapshot the DEM selection as the primitive options the command takes."""
    if not dialog.check_prerequisites():
        raise ValueError("Select a project folder and DEM first.")
    combo = dialog.input_combo("dem")
    source = combo.source_uri()
    if not source:
        raise ValueError("The selected DEM has no readable source.")
    data = combo.currentData()
    configured_crs = data.get("source_crs", "") if isinstance(data, dict) else ""
    layer = combo.currentLayer()
    folder = Path(temp_morphology_folder(dialog.project_folder))
    return {
        "source": source,
        "output_folder": str(folder),
        "source_crs": configured_crs or (
            layers.crs_string(layer.crs()) if layer is not None else ""
        ),
        "target_crs": layers.crs_string(dialog.get_crs()),
        "reprojected_path": str(folder / "dem_reprojected.nc"),
        "header": grid.common_l0_header(dialog),
    }


def prepare_filled_dem(dialog) -> str:
    """Synchronously ensure the filled DEM exists, for calculations that need it now."""
    result = commands.dem_derivatives(None, dem_options(dialog))
    path_ = str(result["filled_dem"])
    lifecycle.register(dialog, path_, "1_DEM_Filled")
    if not result.get("reused"):
        dialog.log_message("DEM derivatives prepared successfully.")
    return path_


def start_dem_derivatives(dialog, *, key="execute-all-dem-derivatives", controls=(), done=None, failed=None):
    """Fill the DEM and derive slope, aspect, facc and fdir in one pass."""
    try:
        options = dem_options(dialog)
    except Exception as error:
        lifecycle.fail(dialog, "DEM derivatives", error, failed)
        return False

    def ready(result):
        lifecycle.register(dialog, result["filled_dem"], "1_DEM_Filled")
        lifecycle.register(dialog, result["dem_derivatives"], "DEM derivatives")
        if not result.get("reused"):
            dialog.log_message("DEM derivatives prepared successfully.")
        grid.update_l0_resolution_from_dem(dialog)
        if done is not None:
            done(result)

    return dialog.task_coordinator.submit(
        key,
        "Prepare DEM derivatives",
        partial(commands.dem_derivatives, options=options),
        controls=_locked(dialog, controls),
        resource="morphology-files",
        on_success=ready,
        on_error=lambda message: lifecycle.fail(dialog, "DEM derivatives", message, failed),
    )


_HYDROLOGY_NAMES = {
    "flow_accumulation": "2_Flow_Accumulation",
    "flow_accumulation_area": "2_Flow_Accumulation_Area",
    "flow_direction": "2_Flow_Direction",
    "channel_network": "2_Channel_Network",
}


def start_hydrology(
    dialog, *, key="prepare-hydrology", controls=(), channel_path=None,
    threshold=None, direction=True, write_flow=True, done=None, failed=None,
):
    """Derive flow and channel files from the prepared DEM derivatives.

    A `channel_path` is a preview: it is written there and not registered as
    the domain's channel network.
    """
    options = {
        "filled_dem": filled_dem_path(dialog),
        "accumulation": write_flow,
        "area_path": path(dialog, "facc_area.nc") if write_flow else "",
        "direction": direction,
        "channel_path": str(channel_path or channel_network_path(dialog)),
        "threshold_cells": threshold,
    }

    def ready(result):
        for name, output in result.items():
            if name != "channel_network" or not channel_path:
                lifecycle.register(dialog, output, _HYDROLOGY_NAMES[name])
        if done is not None:
            done(result)

    return dialog.task_coordinator.submit(
        key,
        "Prepare flow and channel files",
        partial(commands.hydrology, options=options),
        controls=_locked(dialog, controls),
        resource="morphology-files",
        on_success=ready,
        on_error=lambda message: lifecycle.fail(dialog, "Hydrology", message, failed),
    )


def start_domain_preflight(dialog, controls, done, failed=None):
    """Prepare what the Domain Delineator opens on: DEM derivatives, then channels."""
    try:
        dem_options(dialog)
    except Exception as error:
        lifecycle.fail(dialog, "Domain Delineator", error, failed)
        return False
    if _dem_ready(dialog) and os.path.isfile(channel_network_path(dialog)):
        QtCore.QTimer.singleShot(0, lambda: done({"prepared": True}))
        return True

    def channels(_result=None):
        return start_hydrology(
            dialog,
            key="domain-delineator-preflight",
            controls=controls,
            direction=False,
            done=lambda result: done({"prepared": True, **result}),
            failed=failed,
        )

    if _dem_ready(dialog):
        return channels()
    return start_dem_derivatives(
        dialog, key="domain-delineator-preflight-dem", controls=controls,
        done=channels, failed=failed,
    )
