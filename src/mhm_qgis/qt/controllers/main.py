# -*- coding: utf-8 -*-
"""Widget state for the main form (`mhm_qgis_main.ui`).

Every function takes the dialog as its first argument. `MhmQgisDialog` keeps a
one-line method per name because processors, task bridges and the QGIS display
bridge all hold a reference to the dialog and call these through it.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from qgis.PyQt.QtCore import QDate
    from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QFileDialog,
                                     QMessageBox)
except ImportError:
    from ...standalone import install

    install(force=True)
    from qgis.PyQt.QtCore import QDate
    from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QFileDialog,
                                     QMessageBox)

from ...core.grid import (
    crs_equal,
    display_precision_for_unit,
    format_resolution,
    header_bounds,
    integer_resolution_factor,
    possible_resolutions,
    read_header_file,
    resolution_in_crs,
)
from ..dialogs.input_selection import (INPUT_EXTENSIONS, InputComboAdapter,
                                LaiNetcdfInputDialog, MhmReadyInputDialog,
                                SingleLayerInputDialog, loaded_qgis_items,
                                scan_project_folders, scan_project_inputs)
from ...core.meteorology.forcing import MeteoFolderSpec
from ...core.meteorology.forcing import inspect_meteo_folder_cached
from ...dependency_bootstrap import configure_qtpy_api
from ...core.morphology.hydrology.outlets import StationIdError, outlet_ids_from_layer
from ...core.handlers.state import processing
from ...core.handlers.store.layout import ensure_project_structure
from ...core.handlers.store.morphology import land_cover_periods, resolve_display_output
from ...core.handlers.store.paths import (
    data_folder, master_data_folder, plugin_root, version_key, workspace_folder,
)
from ..reporting import error_detail


DISPLAY_KEYS = (
    "dem",
    "slope",
    "aspect",
    "flow_accumulation",
    "flow_direction",
    "idgauges",
    "land_cover",
    "soil",
    "geology",
    "lai",
    "domain_mask",
)


def selected_version(dialog) -> str:
    """Return the selected mHM version, defaulting to v6."""
    combo = getattr(dialog, "comboBox_mHMversion", None)
    return (combo.currentText().strip() if combo is not None else "") or "6.0"


def refresh_observation_stations(dialog):
    from ...core.post.graph import stations

    combo = getattr(dialog, "comboBox_displayOutletStation", None)
    if combo is None:
        return
    selected = combo.currentText()
    blocked = combo.blockSignals(True)
    try:
        combo.clear()
        for station in stations(dialog.project_folder):
            combo.addItem(station["id"], station["path"])
        index = combo.findText(selected)
        if index >= 0:
            combo.setCurrentIndex(index)
    finally:
        combo.blockSignals(blocked)
    combo.setEnabled(combo.count() > 0)
    dialog.pushButton_observationDisplay_discharge.setToolTip(
        "Plot discharge observations for the selected station"
    )
    dialog.pushButton_observationDisplay_discharge.setEnabled(combo.count() > 0)


def display_observation_discharge(dialog):
    from ...core.post.graph import discharge_series

    combo = dialog.comboBox_displayOutletStation
    if not combo.currentData():
        QMessageBox.warning(dialog, "Discharge", "Save a gauged outlet in the domain delineator first.")
        return
    try:
        timestamps, values = discharge_series(combo.currentData())
        from ..dialogs.discharge_plot import DischargePlotDialog

        if getattr(dialog, "_discharge_plot", None) is None:
            dialog._discharge_plot = DischargePlotDialog(dialog)
        dialog._discharge_plot.show_series(combo.currentText(), timestamps, values)
    except (OSError, ValueError, ImportError) as error:
        dialog.log_message(f"ERROR: Discharge plot failed: {error}")
        QMessageBox.warning(dialog, "Discharge", str(error))


def handle_version_changed(dialog) -> None:
    """Prepare the selected project layout and persist the selection."""
    if dialog.project_folder:
        ensure_project_structure(dialog.project_folder, selected_version(dialog))
        refresh = getattr(dialog, "refresh_advanced_nml_settings", None)
        if refresh is not None:
            refresh()
    dialog.save_input_state()


def edit_namelists(dialog) -> bool:
    """Open nml-tools with mhm_qgis paths, values, and dimensions."""
    if not _ensure_project_folder(dialog):
        return False
    try:
        from ...vSpecific import build_dimensions, build_initial_values

        version = selected_version(dialog)
        workspace = workspace_folder(dialog.project_folder)
        schemas_dir = Path(plugin_root()) / "nml-schemas" / version_key(version)
        ensure_project_structure(dialog.project_folder, version)
        configure_qtpy_api()

        from ...applications.nml_tools_handler import launch_profile_editor

        dialog.log_message(f"Opening namelist editor for mHM {version}...")
        launch_profile_editor(
            schemas_dir=schemas_dir,
            output_dir=workspace,
            initial_values=build_initial_values(version, dialog),
            initial_dimensions=build_dimensions(version, dialog),
            log=dialog.log_message,
        )
        return True
    except Exception as exc:
        dialog.log_message(f"ERROR: Edit Namelists failed. Details: {exc}")
        QMessageBox.critical(dialog, "Configuration Error", str(exc))
        return False


def run_mhm(dialog) -> bool:
    """Run mHM in the selected project folder through its terminal."""
    if not _ensure_project_folder(dialog):
        return False
    ensure_project_structure(dialog.project_folder, selected_version(dialog))
    workspace = workspace_folder(dialog.project_folder)
    terminal = dialog.open_project_terminal()
    if terminal is None:
        return False
    dialog.log_message(f"Running mHM in project directory: {workspace}")
    return terminal.run_command("mhm", cwd=workspace, show=True)


def _ensure_project_folder(dialog) -> bool:
    if dialog.project_folder:
        return True
    QMessageBox.warning(
        dialog,
        "Project Folder Required",
        "Select a project folder before editing namelists.",
    )
    return False


def configure_input_adapters(dialog):
    """Wrap each plain input combo in the layer-combo interface the code uses."""

    input_widgets = {
        "dem": "comboBox_demInput",
        "pour_points": "comboBox_pourPointInput",
        "land_cover": "comboBox_landUseInput",
        "soil": "comboBox_soilInput",
        "geology": "comboBox_geologyInput",
        "lai": "comboBox_laiInput",
    }
    for kind, combo_name in input_widgets.items():
        combo = getattr(dialog, combo_name, None)
        if combo is None:
            if kind not in {"land_cover", "soil", "geology", "lai"}:
                continue
            combo = QComboBox(dialog)
            combo.setObjectName(combo_name)
            combo.hide()
            setattr(dialog, combo_name, combo)
        dialog._input_adapters[kind] = InputComboAdapter(combo, kind, dialog)


def input_combo(dialog, kind):
    """Return the layer-combo adapter for one input kind, or None."""
    return dialog._input_adapters.get(kind)


def configure_morphology_display(dialog):
    """Attach stable keys to the morphology display choices."""
    combo = dialog.comboBox_morphVariableToDisplay
    if combo is not None:
        for index, key in enumerate(DISPLAY_KEYS[: combo.count()]):
            combo.setItemData(index, key)
    editor = dialog.dateTimeEdit_forHistoricalInputs
    if editor is not None:
        editor.setEnabled(False)


def refresh_morphology_date_control(dialog) -> None:
    """Enable the date selector only for historical land cover."""
    editor = dialog.dateTimeEdit_forHistoricalInputs
    combo = dialog.comboBox_morphVariableToDisplay
    if editor is None or combo is None:
        return
    periods = land_cover_periods(dialog.project_folder) if dialog.project_folder else []
    enabled = combo.currentData() == "land_cover" and len(periods) > 1
    editor.setEnabled(enabled)
    if not enabled:
        return
    try:
        first = min(int(period["start_year"]) for period in periods)
        last = max(int(period["end_year"]) for period in periods)
    except (KeyError, TypeError, ValueError):
        editor.setEnabled(False)
        return
    editor.setMinimumDate(QDate(first, 1, 1))
    editor.setMaximumDate(QDate(last, 12, 31))
    editor.setDate(QDate(first, 1, 1))


def display_selected_morphology_layer(dialog) -> None:
    """Resolve the selected output and hand it to the QGIS renderer."""
    if not dialog.project_folder:
        QMessageBox.warning(dialog, "Display Layer", "Select a project folder first.")
        return
    key = dialog.comboBox_morphVariableToDisplay.currentData()
    if not key:
        QMessageBox.warning(dialog, "Display Layer", "Select a morphology layer.")
        return
    year = None
    if dialog.dateTimeEdit_forHistoricalInputs.isEnabled():
        year = dialog.dateTimeEdit_forHistoricalInputs.date().year()
    output = resolve_display_output(dialog.project_folder, key, year=year)
    if output is None:
        QMessageBox.warning(
            dialog,
            "Display Layer",
            "The selected morphology output has not been prepared yet.",
        )
        return
    from ...qgis_bridge.display.morphology import show_output

    show_output(dialog, output)


def configure_input_layer_combo_boxes(dialog):
    """Allow input layer boxes to start empty so layers are chosen deliberately."""

    for adapter in dialog._input_adapters.values():
        adapter.setAllowEmptyLayer(True)
        adapter.setLayer(None)


def refresh_input_sources(dialog):
    """Populate input boxes from QGIS and, when enabled, the project folder."""
    include_files = bool(
        dialog.project_folder and dialog.checkBox_enableFolderSearch.isChecked()
    )
    for kind, adapter in dialog._input_adapters.items():
        combo = adapter.combo_box
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for item in loaded_qgis_items(kind):
            combo.addItem(item.label, item.data)
        if include_files:
            for item in scan_project_inputs(dialog.project_folder, kind):
                combo.addItem(item.label, item.data)
        index = dialog._matching_input_index(combo, previous)
        if (
            index < 0
            and isinstance(previous, dict)
            and previous.get("origin") == "file"
            and previous.get("manual")
            and os.path.isfile(previous.get("path", ""))
        ):
            combo.addItem(
                previous.get("label") or previous["path"],
                previous,
            )
            index = combo.count() - 1
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        if isinstance(previous, dict) and index < 0:
            adapter.layerChanged.emit(adapter.currentLayer())


def populate_pour_point_outlet_fields(dialog, layer=None, preferred=None):
    """Populate the outlet ID field selector from the pour-point layer."""
    combo = dialog.comboBox_pourPointOutletID
    previous = str(preferred or combo.currentText() or "").strip()
    if layer is None:
        layer = dialog.input_combo("pour_points").currentLayer()

    names = []
    try:
        if layer and layer.isValid():
            names = list(layer.fields().names())
    except Exception:
        names = []

    combo.blockSignals(True)
    combo.clear()
    combo.addItem("")
    combo.addItems(names)
    index = combo.findText(previous) if previous else -1
    if index < 0:
        for candidate in range(1, combo.count()):
            if combo.itemText(candidate).casefold() == previous.casefold():
                index = candidate
                break
    if index < 0:
        for candidate in range(1, combo.count()):
            if combo.itemText(candidate).casefold() == "station_id":
                index = candidate
                break
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


def selected_outlet_id_field(dialog):
    """Return the selected pour-point unique ID field."""
    return dialog.comboBox_pourPointOutletID.currentText().strip()


def selected_outlet_ids(dialog):
    """Return validated unique outlet IDs from the selected field."""
    layer = dialog.input_combo("pour_points").currentLayer()
    field_name = dialog.selected_outlet_id_field()
    if not field_name:
        raise StationIdError("Select the pour-point outlet ID field.")
    return outlet_ids_from_layer(layer, field_name)


def selected_input_file_paths(dialog):
    """Return local files already selected elsewhere in the plugin."""
    paths = set()
    for _, widget in dialog.input_layer_widgets():
        source = getattr(widget, "source_path", lambda: "")()
        source = str(source or "").split("|", 1)[0]
        if os.path.isfile(source):
            paths.add(os.path.normcase(os.path.abspath(source)))
    for config in dialog._categorical_lookup_configs.values():
        path = str(config.get("lookup_table", "") or "")
        if os.path.isfile(path):
            paths.add(os.path.normcase(os.path.abspath(path)))
    for _, widget in dialog.input_text_widgets():
        path = widget.text().strip()
        if os.path.isfile(path):
            paths.add(os.path.normcase(os.path.abspath(path)))
    for value in dialog._advanced_inputs.values():
        data = value.as_dict() if hasattr(value, "as_dict") else value
        for path in dialog._nested_existing_paths(data):
            paths.add(os.path.normcase(os.path.abspath(path)))
    if os.path.isfile(dialog._land_cover_ready_source):
        paths.add(
            os.path.normcase(os.path.abspath(dialog._land_cover_ready_source))
        )
    return paths


def meteo_input_widgets(dialog):
    """Return folder/source widgets for the three meteorology inputs."""
    return (
        (
            "precipitation",
            dialog.comboBox_precipitationFile,
            dialog.comboBox_precipitationDataSource,
        ),
        (
            "temperature",
            dialog.comboBox_temperatureFile,
            dialog.comboBox_temperatureDataSource,
        ),
        ("pet", dialog.comboBox_petFile, dialog.comboBox_petDataSource),
    )


def refresh_meteo_folder_sources(dialog):
    """Populate meteo folder boxes from the outer project directory."""
    available = (
        scan_project_folders(dialog.project_folder)
        if dialog.project_folder else ()
    )
    for _, combo, _ in dialog.meteo_input_widgets():
        previous = dialog.selected_folder_path(combo)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("", None)
        for item in available:
            combo.addItem(item.label, item.data)
        index = dialog._folder_combo_index(combo, previous)
        if index < 0 and previous and os.path.isdir(previous):
            dialog._add_folder_combo_item(combo, previous)
            index = combo.count() - 1
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)


def selected_meteo_folder(dialog, kind):
    """Return the selected absolute folder path for one meteo input."""
    for input_kind, combo, _ in dialog.meteo_input_widgets():
        if input_kind == kind:
            return dialog.selected_folder_path(combo)
    return ""


def selected_meteo_source(dialog, kind):
    """Return a stable internal source token for one meteo input."""
    for input_kind, _, combo in dialog.meteo_input_widgets():
        if input_kind != kind:
            continue
        text = combo.currentText().strip().lower()
        if text.replace("_", " ") == "mhm ready":
            return "mhm_ready"
        if text.replace("-", "").replace("_", "") == "era5land":
            return "era5land"
    return ""


def selected_folder_path(combo):
    data = combo.currentData()
    if isinstance(data, dict):
        path = data.get("path", "")
    else:
        path = data if isinstance(data, str) else ""
    return os.path.abspath(path) if path else ""


def _folder_combo_index(combo, path):
    if not path:
        return -1
    normalized = os.path.normcase(os.path.abspath(path))
    for index in range(combo.count()):
        data = combo.itemData(index)
        candidate = data.get("path", "") if isinstance(data, dict) else data
        if candidate and os.path.normcase(os.path.abspath(candidate)) == normalized:
            return index
    return -1


def _add_folder_combo_item(dialog, combo, folder):
    folder = os.path.abspath(folder)
    label = folder
    if dialog.project_folder:
        try:
            relative = os.path.relpath(folder, dialog.project_folder)
            if relative != ".." and not relative.startswith(f"..{os.sep}"):
                label = relative.replace("\\", "/")
        except ValueError:
            pass
    combo.addItem(label, {"origin": "folder", "path": folder, "manual": True})


def browse_meteo_input_folder(dialog, kind):
    """Browse for, add, and select one meteorology input folder."""
    folder = QFileDialog.getExistingDirectory(
        dialog,
        f"Select {kind.title()} Data Folder",
        dialog.selected_meteo_folder(kind) or dialog.project_folder or "",
    )
    if not folder:
        return
    for input_kind, combo, _ in dialog.meteo_input_widgets():
        if input_kind != kind:
            continue
        index = dialog._folder_combo_index(combo, folder)
        if index < 0:
            dialog._add_folder_combo_item(combo, folder)
            index = combo.count() - 1
        combo.setCurrentIndex(index)
        dialog.log_message(
            f"{kind.title()} data folder selected: {os.path.abspath(folder)}"
        )
        return


def handle_meteo_input_changed(dialog, kind):
    """Persist and inspect a changed meteorology folder/source pair."""
    if dialog._loading_input_state:
        return
    dialog.save_input_state()
    dialog.invalidate_meteo_morph_setup()
    dialog.inspect_meteo_selection(kind, show_errors=True)


def selected_meteo_crs(dialog):
    """Return the selected CRS in a form accepted by pyproj."""
    crs = dialog.get_crs()
    if crs is None or not crs.isValid():
        return ""
    authid = crs.authid()
    if authid:
        return authid
    to_wkt = getattr(crs, "toWkt", None)
    return to_wkt() if callable(to_wkt) else ""


def meteo_folder_spec(dialog, kind, required=True):
    """Return one validated folder/source selection."""
    folder = dialog.selected_meteo_folder(kind)
    source = dialog.selected_meteo_source(kind)
    if not folder and not required:
        return None
    if not folder:
        raise ValueError(f"Select the {kind} data folder.")
    if not source:
        raise ValueError(f"Select the {kind} data source.")
    if not os.path.isdir(folder):
        raise ValueError(f"The {kind} data folder does not exist:\n{folder}")
    return MeteoFolderSpec(
        kind=kind,
        folder=Path(folder),
        source=source,
        crs=(
            dialog.selected_meteo_crs()
            if source == "mhm_ready" else None
        ),
    )


def selected_meteo_specs(dialog):
    """Return required precipitation/temperature and optional PET inputs."""
    precipitation = dialog.meteo_folder_spec("precipitation")
    temperature = dialog.meteo_folder_spec("temperature")
    pet = dialog.meteo_folder_spec("pet", required=False)
    return precipitation, temperature, pet


def clear_precipitation_resolution_labels(dialog):
    """Clear the raw precipitation resolution display."""
    for name in (
            "label_precipitationResolutionValue",
            "label_precipitationResolutionUnit",
            "label_precipitationResolutionMultiplier"):
        label = getattr(dialog, name, None)
        if label is not None:
            label.setText("")


def inspect_meteo_selection(dialog, kind, show_errors=False):
    """Validate one selected meteo folder and refresh its metadata."""
    folder = dialog.selected_meteo_folder(kind)
    source = dialog.selected_meteo_source(kind)
    if not folder or not source:
        dialog._meteo_inspections.pop(kind, None)
        if kind == "precipitation":
            dialog.clear_precipitation_resolution_labels()
        return None

    try:
        metadata = inspect_meteo_folder_cached(
            dialog.project_folder,
            folder,
            kind,
            source,
            crs_fallback=(
                dialog.selected_meteo_crs()
                if source == "mhm_ready" else None
            ),
            log=dialog.log_message,
        )
        dialog._meteo_inspections[kind] = metadata
        if kind == "precipitation":
            converted = resolution_in_crs(
                metadata,
                dialog.selected_meteo_crs() or None,
            )
            dialog.label_precipitationResolutionValue.setText(
                format_resolution(converted.resolution, converted.unit)
            )
            dialog.label_precipitationResolutionUnit.setText(converted.unit)
            l0_resolution = dialog.current_l0_resolution()
            multiplier = (
                f"{converted.resolution / l0_resolution:.1f}"
                if l0_resolution else ""
            )
            dialog.label_precipitationResolutionMultiplier.setText(multiplier)
            _set_default_l2_multiplier(dialog, metadata, converted, l0_resolution)
        dialog.log_message(
            f"{kind.title()} metadata: {len(metadata.files)} NetCDF file(s), "
            f"{metadata.shape[1]} x {metadata.shape[0]} cells."
        )
        return metadata
    except Exception as error:
        dialog._meteo_inspections.pop(kind, None)
        if kind == "precipitation":
            dialog.clear_precipitation_resolution_labels()
        dialog.log_message(f"ERROR: Invalid {kind} meteorology input: {error}")
        if show_errors:
            QMessageBox.warning(
                dialog,
                f"Invalid {kind.title()} Data",
                str(error),
            )
        return None


def _matching_input_index(combo, previous):
    if not isinstance(previous, dict):
        return -1
    origin = previous.get("origin")
    identity = (
        previous.get("path")
        if origin == "file"
        else previous.get("layer_id") or previous.get("source")
    )
    for index in range(combo.count()):
        data = combo.itemData(index)
        if not isinstance(data, dict) or data.get("origin") != origin:
            continue
        candidate = (
            data.get("path")
            if origin == "file"
            else data.get("layer_id") or data.get("source")
        )
        if candidate == identity:
            return index
    return -1


def browse_input_file(dialog, kind):
    """Add a manually selected file to one input box."""
    patterns = " ".join(
        f"*{suffix}" for suffix in sorted(INPUT_EXTENSIONS[kind])
    )
    path, _ = QFileDialog.getOpenFileName(
        dialog,
        f"Select {kind.replace('_', ' ').title()} Input",
        dialog.project_folder or "",
        f"Supported files ({patterns});;All files (*)",
    )
    if not path:
        return
    path = os.path.abspath(path)
    if os.path.splitext(path)[1].lower() not in INPUT_EXTENSIONS[kind]:
        QMessageBox.warning(
            dialog,
            "Unsupported Input",
            f"Select a {kind.replace('_', ' ')} file with one of these "
            f"extensions: {', '.join(sorted(INPUT_EXTENSIONS[kind]))}.",
        )
        return
    label = path
    if dialog.project_folder:
        try:
            relative = os.path.relpath(path, dialog.project_folder)
            if relative != ".." and not relative.startswith(f"..{os.sep}"):
                label = relative.replace("\\", "/")
        except ValueError:
            pass
    adapter = dialog._input_adapters[kind]
    combo = adapter.combo_box
    for index in range(combo.count()):
        data = combo.itemData(index)
        if isinstance(data, dict) and data.get("path") == path:
            combo.setCurrentIndex(index)
            return
    combo.addItem(
        label,
        {
            "origin": "file",
            "kind": kind,
            "path": path,
            "label": label,
            "manual": True,
        },
    )
    combo.setCurrentIndex(combo.count() - 1)


def connect_optional_processor_button(dialog, name, label, callback):
    """Connect a processing control when it exists in the active UI."""
    button = getattr(dialog, name, None)
    if button is not None:
        dialog.connect_processor_button(
            button, label, callback, background=True
        )


def handle_l2_multiplier_changed(dialog, value=None):
    """Invalidate prepared setup state when the requested L2 grid changes."""
    if dialog._loading_input_state:
        return
    dialog._l2_multiplier_user_selected = True
    dialog.save_input_state()
    dialog.invalidate_meteo_morph_setup()


def _set_default_l2_multiplier(dialog, metadata, converted, l0_resolution):
    """Propose an exact raw factor until the user has chosen a multiplier."""
    if dialog._l2_multiplier_user_selected or not l0_resolution:
        return
    target_crs = dialog.selected_meteo_crs() or None
    if not crs_equal(metadata.crs, target_crs):
        return
    x_factor = integer_resolution_factor(converted.x_resolution, l0_resolution)
    y_factor = integer_resolution_factor(converted.y_resolution, l0_resolution)
    if x_factor is None or x_factor != y_factor:
        return
    control = dialog.spinBox_L2ResolutionMultiplier
    control.blockSignals(True)
    control.setValue(x_factor)
    control.blockSignals(False)
    dialog.save_input_state()


def handle_model_input_changed(dialog, value=None):
    """Persist model inputs and invalidate a previously completed setup."""
    if dialog._loading_input_state:
        return
    dialog.save_input_state()
    dialog.invalidate_meteo_morph_setup()


def refresh_grid_resolution_controls(dialog):
    """Refresh L0, L2, L1, and L11 controls from current project state."""
    dialog.update_l0_resolution_from_dem()
    dialog.update_l2_resolution_from_metadata()
    dialog.refresh_l1_l11_resolution_options()


def update_l0_resolution_from_dem(dialog, layer=None):
    """Prepare/read the filled DEM and show its resolution as L0."""
    info = dialog.filled_dem_resolution_info()
    dialog._grid_l0_info = info
    if not info:
        dialog._set_resolution_labels("L0", "", "")
        dialog.refresh_l1_l11_resolution_options()
        dialog.update_latlon_button_state()
        return

    dialog._set_resolution_labels(
        "L0",
        format_resolution(info["resolution"], info["unit"]),
        info["unit"],
    )
    if abs(info["x_resolution"] - info["y_resolution"]) > max(info["resolution"], 1.0) * 1e-6:
        dialog.log_message(
            "WARNING: DEM pixels are not square. L0 uses the average of "
            f"x={info['x_resolution']} and y={info['y_resolution']}.")
    dialog.refresh_l1_l11_resolution_options()


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
    if not metadata:
        dialog.update_extent_labels()
        dialog.refresh_l1_l11_resolution_options()
        dialog.update_latlon_button_state()
        return

    dialog.update_extent_labels(metadata)
    dialog.refresh_l1_l11_resolution_options()
    dialog.update_latlon_button_state()


def set_meteo_l2_grid_metadata(dialog, metadata):
    """Store freshly prepared L2 metadata and update resolution controls."""
    dialog.update_l2_resolution_from_metadata(metadata)
    dialog.save_input_state()


def refresh_l1_l11_resolution_options(dialog):
    """Populate L1 and L11 resolution choices from L0/L2 compatibility."""
    l0_resolution = dialog.current_l0_resolution()
    l2_resolution = dialog.current_l2_resolution()
    unit = dialog.current_grid_unit()

    if not dialog._grid_l2_header or not l2_resolution:
        dialog.disable_l1_l11_resolution_options()
        dialog.update_latlon_button_state()
        return

    l1_values = []
    if l0_resolution:
        l1_values = possible_resolutions(l0_resolution, l2_resolution, unit)

    preferred_l1 = dialog._preferred_l1_resolution
    matched_l1 = dialog._populate_resolution_combo(
        dialog.comboBox_L1,
        l1_values,
        preferred_l1,
        unit,
    )
    if preferred_l1 is not None and l1_values and l0_resolution and l2_resolution:
        if not matched_l1:
            dialog.log_resolution_preference_warning(
                "L1",
                preferred_l1,
                l1_values[0],
                unit,
            )
        dialog._preferred_l1_resolution = None
    dialog.label_L1ResolutionUnit.setText(unit if l1_values else "")

    dialog.handle_l1_resolution_changed()


def handle_l1_resolution_changed(dialog):
    """Refresh L1 label and rebuild L11 choices for the selected L1."""
    dialog.update_l1_resolution_label()
    l1_resolution = dialog.current_l1_resolution()
    l2_resolution = dialog.current_l2_resolution()
    unit = dialog.current_grid_unit()

    if not dialog._grid_l2_header or not l2_resolution or not l1_resolution:
        dialog.disable_l11_resolution_options()
        dialog.update_latlon_button_state()
        return

    l11_values = []
    l11_values = possible_resolutions(l1_resolution, l2_resolution, unit)

    preferred_l11 = dialog._preferred_l11_resolution
    matched_l11 = dialog._populate_resolution_combo(
        dialog.comboBox_L11,
        l11_values,
        preferred_l11,
        unit,
    )
    if preferred_l11 is not None and l11_values and l1_resolution and l2_resolution:
        if not matched_l11:
            dialog.log_resolution_preference_warning(
                "L11",
                preferred_l11,
                l11_values[0],
                unit,
            )
        dialog._preferred_l11_resolution = None
    dialog.label_L11ResolutionUnit.setText(unit if l11_values else "")
    dialog.update_l11_resolution_label()
    dialog.update_latlon_button_state()


def disable_l1_l11_resolution_options(dialog):
    """Disable L1/L11 controls until meteo L2 grid metadata exists."""
    dialog.disable_resolution_combo(dialog.comboBox_L1)
    dialog.disable_l11_resolution_options()
    dialog._set_resolution_labels("L1", "", "")


def disable_l11_resolution_options(dialog):
    """Disable L11 controls and clear its labels."""
    dialog.disable_resolution_combo(dialog.comboBox_L11)
    dialog._set_resolution_labels("L11", "", "")


def disable_resolution_combo(dialog, combo_box):
    """Clear and disable a grid-resolution combo box."""
    if combo_box is None:
        return
    try:
        combo_box.blockSignals(True)
    except Exception:
        pass
    combo_box.clear()
    combo_box.setEnabled(False)
    try:
        combo_box.blockSignals(False)
    except Exception:
        pass


def update_l1_resolution_label(dialog):
    """Show the selected L1 relation to L0."""
    value = dialog.current_l1_resolution()
    l0_resolution = dialog.current_l0_resolution()
    label = dialog.label_L1Resolution
    if label is None:
        return
    if value and l0_resolution:
        label.setText(f"{value / l0_resolution:g} x L0")
    else:
        label.setText("")


def update_l11_resolution_label(dialog):
    """Show the selected L11 relation to L1."""
    value = dialog.current_l11_resolution()
    l1_resolution = dialog.current_l1_resolution()
    label = dialog.label_L11Resolution
    if label is None:
        return
    if value and l1_resolution:
        label.setText(f"{value / l1_resolution:g} x L1")
    else:
        label.setText("")
    dialog.update_latlon_button_state()


def current_l0_resolution(dialog):
    """Return the current L0 cell size, unrounded when it is known."""
    if dialog._grid_l0_info:
        return float(
            dialog._grid_l0_info.get("exact_resolution")
            or dialog._grid_l0_info["resolution"]
        )
    return None


def current_l2_resolution(dialog):
    """Return current L2 resolution."""
    if dialog._grid_l2_metadata and dialog._grid_l2_metadata.get("l2_resolution"):
        return float(dialog._grid_l2_metadata["l2_resolution"])
    if dialog._grid_l2_header:
        return float(dialog._grid_l2_header["cellsize"])
    return None


def current_l1_resolution(dialog):
    """Return selected L1 resolution."""
    return dialog._current_combo_resolution(dialog.comboBox_L1)


def current_l11_resolution(dialog):
    """Return selected L11 resolution."""
    return dialog._current_combo_resolution(dialog.comboBox_L11)


def current_grid_unit(dialog):
    """Return the unit shared by the derived grid-resolution controls."""
    if dialog._grid_l2_metadata and dialog._grid_l2_metadata.get("l2_unit"):
        return dialog._grid_l2_metadata["l2_unit"]
    if dialog._grid_l0_info:
        return dialog._grid_l0_info.get("unit", "")
    return ""


def update_extent_labels(dialog, metadata=None):
    """Show the final model extent from the prepared L2 grid header."""
    label_names = (
        "label_minimumEasting",
        "label_maximumEasting",
        "label_minimumNorthing",
        "label_maximumNorthing",
    )
    labels = [getattr(dialog, name, None) for name in label_names]
    if any(label is None for label in labels):
        return

    header = None
    if metadata:
        header = metadata.get("l2_header")
    if header is None:
        header = dialog._grid_l2_header

    if not header:
        for label in labels:
            label.setText("")
        return

    unit = (
        (metadata or {}).get("l2_unit")
        or (dialog._grid_l2_metadata or {}).get("l2_unit")
        or dialog.current_grid_unit()
    )
    precision = display_precision_for_unit(unit)
    xmin, xmax, ymin, ymax = header_bounds(header)
    values = (xmin, xmax, ymin, ymax)
    for label, value in zip(labels, values):
        label.setText(format_resolution(value, unit, precision=precision))


def update_latlon_button_state(dialog):
    """Enable latlon creation once all derived grid levels are available."""
    button = dialog.pushButton_createLatLon
    if button is None:
        return

    enabled = bool(
        dialog.current_l0_resolution()
        and dialog.current_l1_resolution()
        and dialog.current_l11_resolution()
        and dialog._grid_l2_header
    )
    button.setEnabled(enabled)


def _set_resolution_labels(dialog, level, value_text, unit_text):
    """Set value and unit labels for a grid level."""
    value_label = getattr(dialog, f"label_{level}Resolution", None)
    unit_label = getattr(dialog, f"label_{level}ResolutionUnit", None)
    if value_label is not None:
        value_label.setText(value_text or "")
    if unit_label is not None:
        unit_label.setText(unit_text or "")


def _populate_resolution_combo(dialog, combo_box, values, preferred_value=None, unit=None):
    """Populate a resolution combo box while preserving a compatible selection."""
    if combo_box is None:
        return False
    current_value = dialog._current_combo_resolution(combo_box)
    preferred = preferred_value or current_value
    try:
        combo_box.blockSignals(True)
    except Exception:
        pass
    combo_box.clear()
    for value in values:
        combo_box.addItem(format_resolution(value, unit), float(value))
    matched_preferred = preferred is None
    if values:
        selected_index = 0
        if preferred:
            for index, value in enumerate(values):
                if dialog.resolution_values_match(value, preferred, unit):
                    selected_index = index
                    matched_preferred = True
                    break
        combo_box.setCurrentIndex(selected_index)
    combo_box.setEnabled(True)
    try:
        combo_box.blockSignals(False)
    except Exception:
        pass
    return matched_preferred


def log_resolution_preference_warning(
        dialog,
        level,
        saved_value,
        fallback_value,
        unit):
    """Log when a saved L1/L11 choice is no longer selectable."""
    precision = dialog.resolution_state_precision(unit)
    dialog.log_message(
        "WARNING: Saved "
        f"{level} resolution {format_resolution(saved_value, unit, precision)} "
        f"{unit or ''} is not compatible with the current grid. "
        f"Using {format_resolution(fallback_value, unit)} {unit or ''}."
    )


def _current_combo_resolution(dialog, combo_box):
    """Return current numeric resolution from a combo box."""
    if combo_box is None or combo_box.count() == 0:
        return None
    try:
        data = combo_box.currentData()
        if data is not None:
            return float(data)
    except Exception:
        pass
    try:
        return float(combo_box.currentText())
    except (TypeError, ValueError):
        return None


def connect_processor_button(
    dialog, button, action_name, callback, *, background=False
):
    """Connect a button to a processor callback with input path logging."""
    if background:
        button.clicked.connect(
            lambda checked=False, name=action_name, cb=callback, control=button:
            dialog.run_background_processor_action(name, cb, control)
        )
    else:
        button.clicked.connect(
            lambda checked=False, name=action_name, cb=callback: dialog.run_processor_action(
                name, cb
            )
        )


def _background_processor_failed(dialog, action_name, message):
    detail = error_detail(message)
    dialog.log_message(f"ERROR: {action_name}: {message}")
    QMessageBox.critical(dialog, action_name, detail)


def handle_domain_definition_type(dialog, index):
    """Dispatch the selected domain-definition workflow."""
    if dialog._loading_input_state:
        return
    previous = dialog._domain_definition_mode
    previous_dem = dialog.checkBox_DEMdomain.isChecked()
    if int(index) == 1:
        dialog._domain_definition_mode = dialog.comboBox_domainDefinitionType.currentText()
        dialog.save_input_state()
        dialog.open_domain_delineator()
        return
    dialog.checkBox_DEMdomain.setChecked(True)
    if dialog.open_domain_assignment():
        dialog._domain_definition_mode = dialog.comboBox_domainDefinitionType.currentText()
        dialog.save_input_state()
        return
    dialog.checkBox_DEMdomain.setChecked(previous_dem)
    combo = dialog.comboBox_domainDefinitionType
    combo.blockSignals(True)
    combo.setCurrentIndex(combo.findText(previous) if previous else -1)
    combo.blockSignals(False)
    dialog.save_input_state()


def _capture_workflow_button_default_styles(dialog):
    """Remember original button styles so saved states can be cleared."""
    for workflow_key in dialog.morphology_workflow_specs():
        button = dialog.morphology_workflow_button(workflow_key)
        if button is not None:
            dialog._workflow_button_default_styles[workflow_key] = (
                button.styleSheet()
            )


def morphology_workflow_button(dialog, workflow_key):
    """Return the button associated with a morphology workflow."""
    spec = dialog.morphology_workflow_specs().get(workflow_key, {})
    for button_name in (spec.get("button"), spec.get("fallback_button")):
        if not button_name:
            continue
        button = getattr(dialog, button_name, None)
        if button is not None:
            return button
    return None


def set_meteo_setup_controls_enabled(dialog, enabled):
    """Freeze all run-affecting dialog controls during the combined run."""
    dialog.pushButton_BrowseProjectFolder.setEnabled(enabled)
    dialog.tabWidget_steps.setEnabled(enabled)
    dialog.stackedWidget.setEnabled(enabled)


def set_execute_all_button_state(dialog, status):
    """Reflect execute-all state on the toolbar-style button."""
    dialog.set_morphology_workflow_button_state("execute_all", status)


def set_morphology_workflow_button_state(dialog, workflow_key, status):
    """Reflect workflow state on its toolbar-style button."""
    button = dialog.morphology_workflow_button(workflow_key)
    if button is None:
        return

    if status == "running":
        button.setEnabled(False)
        button.setStyleSheet(
            "QPushButton {"
            "text-align: left;"
            "background-color: #f6c453;"
            "border: 1px solid #a66f00;"
            "border-radius: 3px;"
            "}"
        )
        return

    button.setEnabled(True)
    if status == "completed":
        button.setStyleSheet(
            "QPushButton {"
            "text-align: left;"
            "background-color: #2e7d32;"
            "border: 1px solid #1b5e20;"
            "border-radius: 3px;"
            "}"
        )
    elif status == "failed":
        button.setStyleSheet(
            "QPushButton {"
            "text-align: left;"
            "background-color: #c62828;"
            "border: 1px solid #8e0000;"
            "border-radius: 3px;"
            "}"
        )
    else:
        button.setStyleSheet(
            dialog._workflow_button_default_styles.get(workflow_key, "")
        )


def refresh_execute_all_button_state(dialog):
    """Restore execute-all button styling from project processing state."""
    dialog.refresh_morphology_workflow_button_state("execute_all")


def refresh_morphology_workflow_button_states(dialog):
    """Restore all morphology workflow button styles from project state."""
    for workflow_key in dialog.morphology_workflow_specs():
        dialog.refresh_morphology_workflow_button_state(workflow_key)


def refresh_morphology_workflow_button_state(dialog, workflow_key):
    """Restore one morphology workflow button style from project state."""
    if dialog.running_morphology_workflow_key() == workflow_key:
        return

    workflow = processing.workflow(dialog.project_folder, workflow_key)
    if workflow.get("status") == "completed":
        if (
                workflow_key == "meteo_morph_setup"
                and dialog.project_folder
                and not os.path.isfile(
                    os.path.join(
                        master_data_folder(dialog.project_folder),
                        "latlon.nc",
                    )
                )):
            dialog.invalidate_meteo_morph_setup()
            return
        dialog.set_morphology_workflow_button_state(workflow_key, "completed")
    elif workflow.get("status") == "failed":
        dialog.set_morphology_workflow_button_state(workflow_key, "failed")
    else:
        dialog.set_morphology_workflow_button_state(workflow_key, "")


def categorical_type_combo(dialog, kind):
    """Return the land-cover, soil, or geology input-type combo box."""
    name = {
        "lc": "comboBox_landUseInputType",
        "soil": "comboBox_soil_inputType",
        "geology": "comboBox_geology_inputType",
        "lai": "comboBox_lai_inputType",
    }[kind]
    combo = getattr(dialog, name, None)
    if combo is None and kind == "lc":
        combo = dialog.comboBox_landUseInputType
    return combo


def categorical_input_mode(dialog, kind):
    """Return the selected categorical input mode."""
    return dialog.categorical_type_combo(kind).currentText().strip()


def categorical_lookup_config(dialog, kind):
    """Return the accepted lookup-table selection for one data type."""
    config = dialog._categorical_lookup_configs.get(kind)
    return dict(config) if config else None


def categorical_source_config(dialog, kind):
    """Return the dialog-owned source for a categorical workflow."""
    if dialog.categorical_input_mode(kind).strip().lower() == "mhm ready":
        config = dialog._categorical_ready_configs.get(kind)
    else:
        config = dialog._categorical_lookup_configs.get(kind)
    return dict(config) if isinstance(config, dict) else None


def lai_netcdf_config(dialog):
    return dict(dialog._lai_input_config)


def _exec_dialog(input_dialog):
    """Run a modal input dialog on Qt5 (`exec_`) and Qt6 (`exec`)."""
    execute = getattr(input_dialog, "exec", None) or input_dialog.exec_
    return execute()


def handle_categorical_type(dialog, kind, text):
    """Open the lookup dialog immediately when lookup mode is selected."""
    text = str(text or "").strip()
    previous = dialog._categorical_modes.get(kind, "")
    if dialog._loading_input_state:
        dialog._categorical_modes[kind] = text
        return
    normalized = text.lower()
    if kind == "lai":
        dialog.handle_lai_input_type(text, previous)
        return
    if kind == "lc":
        dialog.handle_land_use_input_type(text, previous)
        return
    if kind == "soil" and "multi-horizon" in normalized:
        dialog.handle_multi_horizon_soil_input(text, previous)
        return
    if not dialog.project_folder:
        QMessageBox.warning(
            dialog,
            "Project Folder Required",
            "Select a project folder before configuring a lookup table.",
        )
        dialog._restore_categorical_mode(kind, previous)
        return

    if normalized == "mhm ready":
        input_dialog = MhmReadyInputDialog(
            dialog.project_folder,
            kind,
            dialog,
            initial=dialog._categorical_ready_configs.get(kind),
        )
    elif "lookup table" in normalized:
        input_dialog = SingleLayerInputDialog(
            dialog.project_folder,
            kind,
            dialog,
            initial=dialog._categorical_lookup_configs.get(kind),
        )
    else:
        dialog._categorical_modes[kind] = text
        dialog.save_input_state()
        dialog.invalidate_meteo_morph_setup()
        return
    if _exec_dialog(input_dialog) != QDialog.Accepted or (
        input_dialog.selected_config() is None
    ):
        dialog._restore_categorical_mode(kind, previous)
        return

    config = input_dialog.selected_config()
    if normalized == "mhm ready":
        dialog._categorical_ready_configs[kind] = {
            "input_path": config.input_path,
            "classdefinition_path": config.classdefinition_path,
        }
        dialog._categorical_lookup_configs.pop(kind, None)
    else:
        dialog._categorical_lookup_configs[kind] = {
            "input_path": config.input_path,
            "lookup_table": config.lookup_table,
            "mapping_field": config.mapping_field,
            "class_field": config.class_field,
        }
        dialog._categorical_ready_configs.pop(kind, None)
    dialog._categorical_modes[kind] = text
    if kind == "soil":
        dialog._advanced_inputs.pop("soil", None)
        dialog._save_standard_soil_nml_input(
            "mhm_ready" if normalized == "mhm ready" else "single_categorical"
        )
    dialog.save_input_state()
    dialog.invalidate_meteo_morph_setup()


def handle_lai_input_type(dialog, text, previous=""):
    """Collect LAI NetCDF or future categorical input configuration."""
    if not dialog.project_folder:
        QMessageBox.warning(
            dialog, "Project Folder Required", "Select a project folder first."
        )
        dialog._restore_categorical_mode("lai", previous)
        return
    normalized = str(text or "").strip().lower()
    if "netcdf" in normalized:
        input_dialog = LaiNetcdfInputDialog(
            dialog.project_folder,
            dialog,
            initial=dialog._lai_input_config,
        )
    else:
        input_dialog = SingleLayerInputDialog(
            dialog.project_folder,
            "lai",
            dialog,
            initial=dialog._categorical_lookup_configs.get("lai"),
        )
    if _exec_dialog(input_dialog) != QDialog.Accepted or (
        input_dialog.selected_config() is None
    ):
        dialog._restore_categorical_mode("lai", previous)
        return
    config = input_dialog.selected_config()
    if "netcdf" in normalized:
        dialog._lai_input_config = {
            "input_path": config.input_path,
            "target_timestep": config.target_timestep,
        }
        dialog._categorical_lookup_configs.pop("lai", None)
        from ...applications.mhm_tools_handler import lai_time_step
        from ...core.handlers.state.nml_settings import update_section

        update_section(
            dialog.project_folder,
            "lai",
            {
                "mode": "netcdf",
                "source_path": config.input_path,
                "source_variable": "",
                "target_timestep": config.target_timestep,
                "time_step": lai_time_step(config.target_timestep),
                "output_path": "data/master/lai/lai.nc",
                "variable": "lai",
            },
        )
    else:
        dialog._categorical_lookup_configs["lai"] = {
            "input_path": config.input_path,
            "lookup_table": config.lookup_table,
            "mapping_field": config.mapping_field,
            "class_field": config.class_field,
        }
        dialog._lai_input_config = {}
        from ...core.handlers.state.nml_settings import update_section

        update_section(
            dialog.project_folder,
            "lai",
            {
                "mode": "single_categorical",
                **dialog._categorical_lookup_configs["lai"],
                "processing_status": "not_implemented",
            },
        )
    dialog._categorical_modes["lai"] = str(text)
    dialog.save_input_state()
    dialog.invalidate_meteo_morph_setup()


def handle_land_use_input_type(dialog, text, previous=""):
    """Collect the selected ready, single, or historical land-use input."""
    if not dialog.project_folder:
        QMessageBox.warning(
            dialog,
            "Project Folder Required",
            "Select a project folder before configuring land use.",
        )
        dialog._restore_categorical_mode("lc", previous)
        return
    normalized = str(text).strip().lower()
    if normalized == "mhm ready":
        input_dialog = MhmReadyInputDialog(
            dialog.project_folder,
            "lc",
            dialog,
            initial=dialog._categorical_ready_configs.get("lc"),
        )
        if _exec_dialog(input_dialog) != QDialog.Accepted or (
            input_dialog.selected_config() is None
        ):
            dialog._restore_categorical_mode("lc", previous)
            return
        config = input_dialog.selected_config()
        dialog._categorical_ready_configs["lc"] = {
            "input_path": config.input_path,
            "classdefinition_path": "",
        }
        dialog._categorical_lookup_configs.pop("lc", None)
        dialog._advanced_inputs.pop("land_cover", None)
        dialog._land_cover_ready_source = config.input_path
    elif "single categorical" in normalized:
        input_dialog = SingleLayerInputDialog(
            dialog.project_folder,
            "lc",
            dialog,
            initial=dialog._categorical_lookup_configs.get("lc"),
        )
        if _exec_dialog(input_dialog) != QDialog.Accepted or (
            input_dialog.selected_config() is None
        ):
            dialog._restore_categorical_mode("lc", previous)
            return
        config = input_dialog.selected_config()
        dialog._categorical_lookup_configs["lc"] = {
            "input_path": config.input_path,
            "lookup_table": config.lookup_table,
            "mapping_field": config.mapping_field,
            "class_field": config.class_field,
        }
        dialog._categorical_ready_configs.pop("lc", None)
        dialog._advanced_inputs.pop("land_cover", None)
        dialog._land_cover_ready_source = ""
    else:
        from ..dialogs.advanced_inputs import HistoricalLandUseDialog

        initial = dialog._advanced_inputs.get("land_cover")
        input_dialog = HistoricalLandUseDialog(
            dialog.project_folder,
            dialog,
            initial=initial,
            all_time="single layer" in str(text).lower(),
        )
        if _exec_dialog(input_dialog) != QDialog.Accepted:
            dialog._restore_categorical_mode("lc", previous)
            return
        value = input_dialog.selected_input()
        dialog._advanced_inputs["land_cover"] = value
        dialog._land_cover_ready_source = ""
        dialog._save_land_cover_nml_input(value)
    dialog._categorical_modes["lc"] = str(text)
    dialog.save_input_state()
    dialog.invalidate_meteo_morph_setup()
    dialog.refresh_morphology_date_control()


def handle_multi_horizon_soil_input(dialog, text, previous=""):
    """Collect and persist the multi-horizon soil source configuration."""
    if not dialog.project_folder:
        QMessageBox.warning(
            dialog,
            "Project Folder Required",
            "Select a project folder before configuring soil inputs.",
        )
        dialog._restore_categorical_mode("soil", previous)
        return
    from ..dialogs.advanced_inputs import MultiHorizonSoilDialog

    input_dialog = MultiHorizonSoilDialog(
        dialog.project_folder,
        dialog,
        initial=dialog._advanced_inputs.get("soil"),
    )
    if _exec_dialog(input_dialog) != QDialog.Accepted:
        dialog._restore_categorical_mode("soil", previous)
        return
    value = input_dialog.selected_input()
    dialog._advanced_inputs["soil"] = value
    dialog._categorical_modes["soil"] = str(text)
    dialog._save_soil_nml_input(value)
    dialog.save_input_state()
    dialog.invalidate_meteo_morph_setup()


def uses_advanced_categorical_input(dialog, kind):
    """Return whether processing should use the advanced input pipeline."""
    text = dialog.categorical_input_mode(kind).lower()
    return (
        (kind == "lc" and "historical" in text)
        or (kind == "soil" and "multi-horizon" in text)
    )


def _restore_categorical_mode(dialog, kind, text):
    combo = dialog.categorical_type_combo(kind)
    combo.blockSignals(True)
    index = combo.findText(text) if text else -1
    combo.setCurrentIndex(index)
    combo.blockSignals(False)


def input_layer_widgets(dialog):
    """Return persistent layer input widgets and their state keys."""
    widgets = []
    for key, kind in (
        ("dem", "dem"),
        ("pour_points", "pour_points"),
        ("soil", "soil"),
        ("land_cover", "land_cover"),
        ("geology", "geology"),
        ("lai_class", "lai"),
    ):
        widget = dialog.input_combo(kind)
        if widget is not None:
            widgets.append((key, widget))

    return widgets


def input_text_widgets(dialog):
    """Return persistent text/path input widgets and their state keys."""
    widgets = []

    for key, widget_name in (
        ("lai_file", "lineEdit_lai_file"),
    ):
        widget = getattr(dialog, widget_name, None)
        if widget is not None:
            widgets.append((key, widget))
    return widgets


def on_tab_changed(dialog, index):
    """Switches the stacked widget page when the tab is changed."""
    page = dialog.page_for_tab_index(index)
    if page is not None:
        dialog.stackedWidget.setCurrentWidget(page)
    else:
        dialog.stackedWidget.setCurrentIndex(index)
    dialog.log_message(f"Switched to '{dialog.tabWidget_steps.tabText(index)}' tab.")
    # mHM reports nothing when it finishes, so the Outputs tab re-reads the
    # output folder each time it is shown.
    if dialog.tabWidget_steps.widget(index) is dialog.tab_outputs:
        dialog.refresh_output_display()


def page_for_tab_index(dialog, index):
    """Return the stacked page associated with a tab widget index.

    The tabs and the stacked pages are not a one-to-one set: `tab_outputs`
    has no page of its own, so it falls through and the caller keeps the
    index-based mapping.
    """
    tab = dialog.tabWidget_steps.widget(index)
    page_pairs = (
        (dialog.tab_geometry, dialog.page_geometry),
        (dialog.tab_meteo, dialog.page_meteo),
        (dialog.tab_hydro, dialog.page_hydro),
        (dialog.tab_execution, dialog.page_execution),
    )
    for tab_widget, page_widget in page_pairs:
        if tab_widget is tab:
            return page_widget
    return None


def browse_lai_file(dialog):
    """Browse for Leaf Area Index file"""
    if not hasattr(dialog, "lineEdit_lai_file"):
        dialog.log_message("LAI input is now selected from the layer dropdown.")
        return

    file_path, _ = QFileDialog.getOpenFileName(
        dialog,
        "Select Leaf Area Index File",
        "",
        "All Files (*);;GeoTIFF (*.tif *.tiff);;NetCDF (*.nc);;HDF5 (*.h5 *.hdf5)",
    )
    if file_path:
        dialog.lineEdit_lai_file.setText(file_path)
        dialog.log_message(f"LAI file selected: {file_path}")
        dialog.save_input_state()
        dialog.invalidate_meteo_morph_setup()


def get_lai_time_range(dialog):
    """Get the selected LAI time/date for extraction"""
    if hasattr(dialog, "dateEdit") and dialog.dateEdit:
        return dialog.dateEdit.dateTime()
    return None


def get_crs(dialog):
    """Get the selected CRS"""
    return dialog.mProjectionSelectionWidget_crs.crs()


def update_gauged_outlet_count(dialog, layer=None):
    """Show configured gauges, falling back to the layer feature count.

    Reads a combo box and writes a label, so it is controller work; it used to
    be a mixin on the morphology processor, which put a widget reference inside
    the computation layer.
    """
    from ...core.morphology.hydrology.outlets import (
        configured_gauged_outlet_ids,
        feature_count_text,
    )

    if layer is None:
        layer = dialog.input_combo("pour_points").currentLayer()

    configured_ids = configured_gauged_outlet_ids(
        getattr(dialog, "project_folder", None)
    )
    count = (
        str(len(configured_ids))
        if configured_ids is not None
        else feature_count_text(layer)
    )
    if hasattr(dialog, "label_numberOfGaugedOutletsValue"):
        dialog.label_numberOfGaugedOutletsValue.setText(count)
    refresh_observation_stations(dialog)
    return count
