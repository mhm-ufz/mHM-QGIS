"""The common L0/L1/L11/L2 grid on the main form.

The DEM fixes L0, the precipitation reference folder and the multiplier fix L2,
and L1/L11 are chosen between them. A precipitation scan establishes the grid;
changing any input it depends on invalidates it.
"""
from __future__ import annotations

import math
import os

from qgis.PyQt.QtCore import QDateTime, QSignalBlocker
from qgis.PyQt.QtWidgets import QMessageBox

from ...core.executions.morphology import setup as morphology_setup
from ...core.grid import (
    Extent,
    Grid,
    ceil_cellsize,
    crs_equal,
    display_precision_for_unit,
    format_resolution,
    header_bounds,
    header_for_model_level,
    integer_resolution_factor,
    is_geographic_unit,
    l0_header_from_l2,
    possible_resolutions,
    read_header_file,
    resolution_in_crs,
    target_grid_from_header,
    validate_grid_headers,
)
from ...core.handlers.state import processing
from ...core.handlers.state import settings as project_settings
from ...core.handlers.store.paths import data_folder, portable_path
from ...core.meteorology.forcing import MeteoFolderSpec
from ...qgis_bridge import layers as qgis_layers
from ..dialogs.file_picker import open_directory
from . import time
from .version import selected as selected_version
from .workflow_state import invalidate as invalidate_workflows
from .workflow_state import invalidate_meteo
from .workflow_state import set_status as set_workflow_status

# --- Precipitation reference folder ---------------------------------------

def selected_folder_path(combo):
    data = combo.currentData()
    if isinstance(data, dict):
        path = data.get("path", "")
    else:
        path = data if isinstance(data, str) else ""
    return os.path.abspath(path) if path else ""


def precipitation_folder(dialog):
    return selected_folder_path(dialog.comboBox_precipitationFile)


def folder_combo_index(combo, path):
    if not path:
        return -1
    normalized = os.path.normcase(os.path.abspath(path))
    for index in range(combo.count()):
        data = combo.itemData(index)
        candidate = data.get("path", "") if isinstance(data, dict) else data
        if candidate and os.path.normcase(os.path.abspath(candidate)) == normalized:
            return index
    return -1


def select_precipitation_folder(dialog, folder):
    """Select `folder` in the reference combo, adding it when it is new."""
    combo = dialog.comboBox_precipitationFile
    index = folder_combo_index(combo, folder)
    if index < 0:
        folder = os.path.abspath(folder)
        combo.addItem(
            portable_path(dialog.project_folder, folder),
            {"origin": "folder", "path": folder, "manual": True},
        )
        index = combo.count() - 1
    combo.setCurrentIndex(index)


def refresh_precipitation_folder(dialog):
    """Reset the folder box to a blank entry plus the browsed folder."""
    combo = dialog.comboBox_precipitationFile
    previous = selected_folder_path(combo)
    with QSignalBlocker(combo):
        combo.clear()
        combo.addItem("", None)
        if previous and os.path.isdir(previous):
            select_precipitation_folder(dialog, previous)
        else:
            combo.setCurrentIndex(0)


def browse_precipitation_folder(dialog):
    folder = open_directory(dialog, "Select Precipitation Data Folder")
    if folder:
        select_precipitation_folder(dialog, folder)
        dialog.log_message(
            f"Precipitation data folder selected: {os.path.abspath(folder)}"
        )


def precipitation_changed(dialog):
    """Persist a changed reference folder or pattern and drop the old grid."""
    if dialog._loading_input_state:
        return
    dialog.save_input_state()
    invalidate_meteo(dialog)
    dialog._meteo_inspections.pop("pre", None)
    invalidate_grid(dialog)
    clear_precipitation_resolution_labels(dialog)


def precipitation_spec(dialog):
    """Snapshot the accepted precipitation metadata with the current folder."""
    data = dict(dialog._meteo_inputs.get("pre", {}))
    if not data:
        raise ValueError("Configure and scan pre first.")
    data.update(
        folder=precipitation_folder(dialog),
        pattern=dialog.lineEdit_precipitationGlobDefinition.text().strip(),
        crs=dialog.selected_meteo_crs() or None,
    )
    return MeteoFolderSpec(**data).normalized()


def clear_precipitation_resolution_labels(dialog):
    for label in (
        dialog.label_precipitationResolutionValue,
        dialog.label_precipitationResolutionUnit,
        dialog.label_precipitationResolutionMultiplier,
    ):
        label.setText("")


# --- The common grid ------------------------------------------------------

def invalidate_grid(dialog):
    if dialog._loading_input_state:
        return
    dialog._grid_l2_metadata = None
    dialog._grid_l2_header = None
    if dialog.project_folder:
        processing.overlay(dialog.project_folder, {"grid": {}})
        invalidate_workflows(
            dialog,
            "scan_precipitation",
            "execute_all_morphology",
            "execute_all_meteo",
            "execute_all_observations",
            "run_mhm",
        )
    update_extent_labels(dialog)


def grid_fingerprint(dialog):
    from ...core.handlers.file.netcdf.raster import file_path
    from ...core.handlers.state.cache import fingerprint
    from ...core.meteorology.forcing import inspection_fingerprint
    layer = dialog.input_combo("dem").currentLayer()
    return fingerprint([file_path(layer.source())] if layer else [], {
        "crs": dialog.selected_meteo_crs(),
        "precipitation": inspection_fingerprint(precipitation_spec(dialog))[0],
        "gap_limit": project_settings.read(dialog.project_folder)["grid_alignment_gap_limit"],
        "multiplier": dialog.spinBox_L2ResolutionMultiplier.value(), "grid_version": 2})


def common_l0_header(dialog):
    """The L0 header of the established common grid, or None before a scan."""
    return (dialog._grid_l2_metadata or {}).get("l0_header")


def require_common_grid(dialog):
    grid = dialog._grid_l2_metadata or {}
    if not grid or grid.get("configuration_fingerprint") != grid_fingerprint(dialog):
        raise ValueError("Scan precipitation to establish the common DEM/L2 grid first.")
    grid_level_headers(dialog)  # Validate all model resolution levels.
    return grid


def precipitation_scanned(dialog, metadata):
    from ...core.handlers.state.nml_settings import load_settings
    try:
        dialog._meteo_inspections["pre"] = metadata
        update_l0_resolution_from_dem(dialog)
        converted = resolution_in_crs(metadata, dialog.selected_meteo_crs())
        _set_default_l2_multiplier(dialog, metadata, converted, current_l0_resolution(dialog))
        _, _, grid = prepare_meteo_l2_grid(dialog, metadata)
        grid["configuration_fingerprint"] = grid_fingerprint(dialog)
        previous = dialog._grid_l2_metadata or {}
        if previous.get("configuration_fingerprint") != grid["configuration_fingerprint"]:
            processing.mark_workflow(dialog.project_folder, "execute_all_morphology", "pending")
            processing.mark_workflow(dialog.project_folder, "execute_all_meteo", "pending")
        processing.save_grid_metadata(dialog.project_folder, grid)
        update_l2_resolution_from_metadata(dialog, grid)
        dialog.save_input_state()
        dialog.label_precipitationResolutionValue.setText(format_resolution(converted.resolution, converted.unit))
        dialog.label_precipitationResolutionUnit.setText(converted.unit)
        dialog.label_precipitationResolutionMultiplier.setText(str(converted.resolution / current_l0_resolution(dialog)))
        dialog.label_precipitationStartTimeValue.setText(metadata.start_time[:16])
        dialog.label_precipitationEndTimeValue.setText(metadata.end_time[:16])
        if not load_settings(dialog.project_folder).get("time"):
            for key, name in time.TIME_WIDGETS.items():
                text = metadata.end_time if key == "sim_end" else metadata.start_time
                value = QDateTime.fromString(text[:16] if selected_version(dialog).startswith("6") else text[:10] + "T00:00", "yyyy-MM-ddTHH:mm")
                with QSignalBlocker(getattr(dialog, name)):
                    getattr(dialog, name).setDateTime(value)
            time.save(dialog)
        dialog.log_message(f"Scanned {len(metadata.files)} precipitation files ({metadata.source}); common grid saved.")
        set_workflow_status(dialog, "scan_precipitation", "completed")
        return True
    except (OSError, ValueError) as error:
        invalidate_grid(dialog)
        set_workflow_status(
            dialog, "scan_precipitation", "failed", str(error)
        )
        QMessageBox.warning(dialog, "Common grid", str(error))
        return False


def handle_l2_multiplier_changed(dialog, value=None):
    """Invalidate prepared setup state when the requested L2 grid changes."""
    if dialog._loading_input_state:
        return
    dialog._l2_multiplier_user_selected = True
    dialog.save_input_state()
    invalidate_meteo(dialog)
    metadata = dialog._meteo_inspections.get("pre")
    invalidate_grid(dialog)
    if metadata:
        precipitation_scanned(dialog, metadata)


def _set_default_l2_multiplier(dialog, metadata, converted, l0_resolution):
    """Propose an exact raw factor until the user has chosen a multiplier."""
    if dialog._l2_multiplier_user_selected or not l0_resolution:
        return
    if not crs_equal(metadata.crs, dialog.selected_meteo_crs() or None):
        return
    x_factor = integer_resolution_factor(converted.x_resolution, l0_resolution)
    y_factor = integer_resolution_factor(converted.y_resolution, l0_resolution)
    if x_factor is None or x_factor != y_factor:
        return
    with QSignalBlocker(dialog.spinBox_L2ResolutionMultiplier):
        dialog.spinBox_L2ResolutionMultiplier.setValue(x_factor)


def filled_dem_resolution_info(dialog):
    """Read the effective DEM header; never start terrain processing here."""
    from ...core.grid.raster import dem_grid
    layer = dialog.input_combo("dem").currentLayer()
    crs = dialog.get_crs()
    if not layer or not crs or not crs.isValid():
        return None
    return dem_grid(layer.source(), qgis_layers.crs_string(layer.crs()),
                    qgis_layers.crs_string(crs), qgis_layers.crs_unit(crs))


def prepare_meteo_l2_grid(dialog, precipitation_metadata=None):
    """Build the requested L2 grid from the L0 info read by the caller."""
    if not dialog._grid_l0_info:
        raise ValueError("Filled DEM is required before preparing meteorology data.")
    x_resolution = float(dialog._grid_l0_info["exact_x_resolution"])
    y_resolution = float(dialog._grid_l0_info["exact_y_resolution"])
    if abs(x_resolution - y_resolution) > max(x_resolution, y_resolution, 1.0) * 1e-6:
        raise ValueError(
            "The filled DEM must have square L0 cells before preparing "
            f"meteorology data (x={x_resolution}, y={y_resolution})."
        )
    target_crs = dialog.get_crs()
    if target_crs is None or not target_crs.isValid():
        raise ValueError("Please set a valid processing CRS before preparing meteorology data.")
    domain_bounds = header_bounds(dialog._grid_l0_info["header"])
    extent_source = dialog.input_combo("dem").currentLayer().source()

    crs_text = qgis_layers.crs_string(target_crs)
    unit = qgis_layers.crs_unit(target_crs)
    raw_resolution = None
    raw_extent = None
    raw_crs = None
    source_file = ""
    if precipitation_metadata is not None:
        converted = resolution_in_crs(precipitation_metadata, crs_text or None)
        raw_resolution = (converted.x_resolution, converted.y_resolution)
        raw_extent = Extent.from_centres(
            precipitation_metadata.bounds,
            precipitation_metadata.x_resolution,
            precipitation_metadata.y_resolution,
        )
        raw_crs = precipitation_metadata.crs
        source_file = str(precipitation_metadata.files[0])
    grid = Grid.for_meteorology(
        dialog._grid_l0_info["header"],
        (domain_bounds,),
        dialog.spinBox_L2ResolutionMultiplier.value(),
        crs=crs_text,
        unit=unit,
        raw_l2_resolution=raw_resolution,
        raw_l2_extent=raw_extent,
        raw_l2_crs=raw_crs,
        gap_limit_fraction=project_settings.read(dialog.project_folder)["grid_alignment_gap_limit"],
    )
    target = target_grid_from_header(grid.l2_header, crs_text or None)
    metadata = grid.metadata(
        extent_source=os.path.basename(str(extent_source).split("|", 1)[0]),
        source_file=source_file,
    )
    if target.sample_lon is not None:
        metadata["wgs84_bounds"] = (
            float(target.sample_lon.min()),
            float(target.sample_lon.max()),
            float(target.sample_lat.min()),
            float(target.sample_lat.max()),
        )
    if raw_resolution:
        metadata.update({
            "raw_meteo_resolution": sum(raw_resolution) / 2,
            "raw_meteo_x_resolution": raw_resolution[0],
            "raw_meteo_y_resolution": raw_resolution[1],
        })
    dialog.log_message(
        "Meteo L2 grid: "
        f"{format_resolution(metadata.get('l2_resolution'), metadata.get('l2_unit', ''))} "
        f"{metadata.get('l2_unit', '')} "
        f"({metadata.get('l2_ratio_to_l0')} x L0)."
    )
    if grid.requires_l2_resampling and grid.alignment_gap:
        dialog.log_message(
            "Meteo L2 grid requires resampling: alignment gap "
            f"x={grid.alignment_gap[0]:g}, y={grid.alignment_gap[1]:g} "
            f"exceeds {grid.alignment_gap_limit:g}."
        )
    return grid, target, metadata


def grid_level_headers(dialog):
    """Return compatible L0, L1, L11, and L2 headers for latlon.nc."""
    if not dialog._grid_l2_header:
        update_l2_resolution_from_metadata(dialog)
    if not dialog._grid_l2_header:
        raise ValueError("L2 grid is not available. Run and save meteorology data first.")

    l0_resolution = current_l0_resolution(dialog)
    l1_resolution = current_l1_resolution(dialog)
    l11_resolution = current_l11_resolution(dialog)
    if not l0_resolution or not l1_resolution or not l11_resolution:
        raise ValueError("L0, L1, and L11 resolutions must be available.")

    l2_header = dict(dialog._grid_l2_header)
    l2_header["unit"] = current_grid_unit(dialog)
    ratio = int(
        (dialog._grid_l2_metadata or {}).get("l2_ratio_to_l0")
        or round(float(l2_header["cellsize"]) / float(l0_resolution))
    )
    headers = {
        "L0": l0_header_from_l2(l2_header, l0_resolution, ratio),
        "L1": header_for_model_level(l2_header, l1_resolution, l0_resolution, "L1"),
        "L11": header_for_model_level(l2_header, l11_resolution, l0_resolution, "L11"),
        "L2": l2_header,
    }
    return validate_grid_headers(headers)[0]


def process_lat_lon(dialog):
    """Create the v5 coordinate input through the core setup API."""
    dialog.log_selected_input_paths("Create LatLon")
    dialog.save_input_state()
    request = morphology_setup.SetupRequest(
        project_folder=dialog.project_folder,
        headers=grid_level_headers(dialog),
        crs=dialog.selected_meteo_crs(),
    )
    outputs = morphology_setup.latlon(request, log=dialog.log_message)
    if outputs:
        dialog.log_message(f"latlon.nc created successfully: {outputs[0]}")
    return bool(outputs)


# --- Resolution controls --------------------------------------------------

def refresh_grid_resolution_controls(dialog):
    """Refresh L0, L2, L1, and L11 controls from current project state."""
    update_l0_resolution_from_dem(dialog, refresh=False)
    update_l2_resolution_from_metadata(dialog)


def update_l0_resolution_from_dem(dialog, layer=None, *, refresh=True):
    """Read the source DEM header and show its resolution as L0."""
    info = filled_dem_resolution_info(dialog)
    dialog._grid_l0_info = info
    if not info:
        _set_resolution_labels(dialog, "L0", "", "")
    else:
        _set_resolution_labels(
            dialog, "L0", format_resolution(info["resolution"], info["unit"]), info["unit"]
        )
        if abs(info["x_resolution"] - info["y_resolution"]) > max(info["resolution"], 1.0) * 1e-6:
            dialog.log_message(
                "WARNING: DEM pixels are not square. L0 uses the average of "
                f"x={info['x_resolution']} and y={info['y_resolution']}.")
    if refresh:
        refresh_l1_l11_resolution_options(dialog)


def update_l2_resolution_from_metadata(dialog, metadata=None):
    """Show L2 resolution from saved meteo grid metadata or existing headers."""
    if metadata is None and dialog.project_folder:
        metadata = processing.grid_metadata(dialog.project_folder)

    header = None
    unit = ""
    if metadata:
        header = metadata.get("l2_header")
        unit = metadata.get("l2_unit", "")
        if not unit and dialog._grid_l0_info:
            unit = dialog._grid_l0_info.get("unit", "")
        if header:
            # Keep the exact L2 cell size. It is n x the filled DEM cell
            # size by construction, and re-rounding it here breaks that
            # relationship for repeating values such as 120 x 1/1200 deg.
            header = dict(header)
            header["unit"] = unit
            metadata["l2_header"] = header
            metadata["l2_resolution"] = header["cellsize"]
            metadata["l2_unit"] = unit
    elif dialog.project_folder:
        unit = dialog._grid_l0_info.get("unit", "") if dialog._grid_l0_info else ""
        header = read_header_file(
            os.path.join(data_folder(dialog.project_folder), "meteo", "pre", "header.txt"),
            unit=unit,
        )
        if header:
            metadata = {
                "l2_resolution": header["cellsize"],
                "l2_unit": unit,
                "l2_header": header,
            }

    dialog._grid_l2_metadata = metadata
    dialog._grid_l2_header = header
    update_extent_labels(dialog, metadata)
    refresh_l1_l11_resolution_options(dialog)


def refresh_l1_l11_resolution_options(dialog):
    """Populate L1 and L11 resolution choices from L0/L2 compatibility."""
    l0_resolution = current_l0_resolution(dialog)
    l2_resolution = current_l2_resolution(dialog)
    unit = current_grid_unit(dialog)

    if not dialog._grid_l2_header or not l2_resolution:
        _populate_resolution_input(dialog, "L1", [])
        disable_l11_resolution_options(dialog)
        update_l1_resolution_label(dialog)
        return

    l1_values = possible_resolutions(l0_resolution, l2_resolution, unit) if l0_resolution else []
    preferred_l1 = dialog._preferred_l1_resolution
    matched_l1 = _populate_resolution_input(dialog, "L1", l1_values, preferred_l1, unit)
    if preferred_l1 is not None and l1_values and l0_resolution:
        if not matched_l1:
            log_resolution_preference_warning(dialog, "L1", preferred_l1, l1_values[0], unit)
        dialog._preferred_l1_resolution = None

    handle_l1_resolution_changed(dialog)


def handle_l1_resolution_changed(dialog):
    """Refresh L1 label and rebuild L11 choices for the selected L1."""
    update_l1_resolution_label(dialog)
    l1_resolution = current_l1_resolution(dialog)
    l2_resolution = current_l2_resolution(dialog)
    unit = current_grid_unit(dialog)

    if not dialog._grid_l2_header or not l2_resolution or not l1_resolution:
        disable_l11_resolution_options(dialog)
        return

    l11_values = possible_resolutions(l1_resolution, l2_resolution, unit)
    preferred_l11 = dialog._preferred_l11_resolution
    matched_l11 = _populate_resolution_input(dialog, "L11", l11_values, preferred_l11, unit)
    if preferred_l11 is not None and l11_values:
        if not matched_l11:
            log_resolution_preference_warning(dialog, "L11", preferred_l11, l11_values[0], unit)
        dialog._preferred_l11_resolution = None
    update_l11_resolution_label(dialog)


def disable_l11_resolution_options(dialog):
    """Disable L11 controls and clear its labels."""
    _populate_resolution_input(dialog, "L11", [])
    update_l11_resolution_label(dialog)


def update_l1_resolution_label(dialog):
    """Show the selected L1 relation to L0."""
    value = current_l1_resolution(dialog)
    l0_resolution = current_l0_resolution(dialog)
    dialog.label_L1Resolution.setToolTip(
        f"{value / l0_resolution:g} x L0" if value and l0_resolution else ""
    )


def update_l11_resolution_label(dialog):
    """Show the selected L11 relation to L1."""
    value = current_l11_resolution(dialog)
    l1_resolution = current_l1_resolution(dialog)
    dialog.label_L11Resolution.setToolTip(
        f"{value / l1_resolution:g} x L1" if value and l1_resolution else ""
    )
    update_latlon_button_state(dialog)


def current_l0_resolution(dialog):
    """Return the current L0 cell size, unrounded when it is known."""
    if dialog._grid_l0_info:
        return float(
            dialog._grid_l0_info.get("exact_resolution")
            or dialog._grid_l0_info["resolution"]
        )
    return None


def current_l2_resolution(dialog):
    if dialog._grid_l2_metadata and dialog._grid_l2_metadata.get("l2_resolution"):
        return float(dialog._grid_l2_metadata["l2_resolution"])
    if dialog._grid_l2_header:
        return float(dialog._grid_l2_header["cellsize"])
    return None


def current_l1_resolution(dialog):
    return _current_resolution_input(dialog, "L1")


def current_l11_resolution(dialog):
    return _current_resolution_input(dialog, "L11")


def current_grid_unit(dialog):
    """Return the unit shared by the derived grid-resolution controls."""
    if dialog._grid_l2_metadata and dialog._grid_l2_metadata.get("l2_unit"):
        return dialog._grid_l2_metadata["l2_unit"]
    if dialog._grid_l0_info:
        return dialog._grid_l0_info.get("unit", "")
    return ""


def update_extent_labels(dialog, metadata=None):
    """Show the final model extent from the prepared L2 grid header."""
    labels = (
        dialog.label_minimumEasting,
        dialog.label_maximumEasting,
        dialog.label_minimumNorthing,
        dialog.label_maximumNorthing,
    )
    header = (metadata or {}).get("l2_header") or dialog._grid_l2_header
    if not header:
        for label in labels:
            label.setText("")
        return
    unit = (
        (metadata or {}).get("l2_unit")
        or (dialog._grid_l2_metadata or {}).get("l2_unit")
        or current_grid_unit(dialog)
    )
    precision = display_precision_for_unit(unit)
    for label, value in zip(labels, header_bounds(header), strict=True):
        label.setText(format_resolution(value, unit, precision=precision))


def update_latlon_button_state(dialog):
    """Enable latlon creation once all derived grid levels are available."""
    dialog.pushButton_createLatLon.setEnabled(bool(
        current_l0_resolution(dialog)
        and current_l1_resolution(dialog)
        and current_l11_resolution(dialog)
        and dialog._grid_l2_header
    ))


def _set_resolution_labels(dialog, level, value_text, unit_text):
    getattr(dialog, f"label_{level}Resolution").setText(value_text or "")
    getattr(dialog, f"label_{level}ResolutionUnit").setText(unit_text or "")


def resolution_state_precision(unit):
    """Return precision used when restoring saved grid-resolution choices."""
    return 8 if is_geographic_unit(unit) else 6


def resolution_values_match(available_value, preferred_value, unit):
    """Return True when two resolutions are equivalent for state restore."""
    precision = resolution_state_precision(unit)
    try:
        available = ceil_cellsize(available_value, unit, precision=precision)
        preferred = ceil_cellsize(preferred_value, unit, precision=precision)
    except (TypeError, ValueError):
        return False
    return abs(available - preferred) <= 10 ** (-precision)


def _populate_resolution_input(dialog, level, values, preferred_value=None, unit=None):
    """Keep exact compatible grid values behind the form's numeric text field."""
    field = getattr(dialog, f"lineEdit_{level}Resolution")
    preferred = preferred_value or _current_resolution_input(dialog, level)
    selected = next(
        (
            value
            for value in values
            if preferred and resolution_values_match(value, preferred, unit)
        ),
        None,
    )
    if selected is None and preferred is not None:
        try:
            candidate = float(preferred)
            selected = candidate if math.isfinite(candidate) and candidate > 0 else None
        except (TypeError, ValueError):
            selected = None
    matched = preferred is None or selected is not None
    selected = selected if selected is not None else (values[0] if values else None)
    field.setProperty("resolutions", values)
    with QSignalBlocker(field):
        field.setText(repr(float(selected)) if selected is not None else "")
    field.setEnabled(bool(values) or selected is not None)
    field.setToolTip(
        "Suggested resolutions: "
        + ", ".join(format_resolution(value, unit) for value in values)
    )
    units = getattr(dialog, f"comboBox_{level}ResolutionUnit")
    units.clear()
    units.addItem(unit or "")
    # Grid units belong to the selected CRS, not a display override.
    units.setEnabled(False)
    return matched


def log_resolution_preference_warning(dialog, level, saved_value, fallback_value, unit):
    """Log when a saved L1/L11 choice is no longer selectable."""
    precision = resolution_state_precision(unit)
    dialog.log_message(
        "WARNING: Saved "
        f"{level} resolution {format_resolution(saved_value, unit, precision)} "
        f"{unit or ''} is not compatible with the current grid. "
        f"Using {format_resolution(fallback_value, unit)} {unit or ''}."
    )


def _current_resolution_input(dialog, level):
    """Return a positive entered cell size; execution checks compatibility."""
    try:
        entered = float(getattr(dialog, f"lineEdit_{level}Resolution").text())
    except (TypeError, ValueError):
        return None
    return entered if math.isfinite(entered) and entered > 0 else None
