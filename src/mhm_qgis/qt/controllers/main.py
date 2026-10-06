"""Input sources, outlets, domain definition and result display on the main form."""
from __future__ import annotations

import os
import traceback

from qgis.PyQt.QtCore import QDateTime
from qgis.PyQt.QtWidgets import QDialog, QMessageBox

from ...core.handlers.store.morphology import land_cover_periods, resolve_display_output
from ...core.handlers.store.paths import portable_path
from ...core.morphology.hydrology.outlets import StationIdError, outlet_ids_from_layer
from ..dialogs.file_picker import open_file
from ..dialogs.input_selection import (
    INPUT_EXTENSIONS,
    InputComboAdapter,
    loaded_qgis_items,
    raster_details,
    scan_project_inputs,
)
from ..reporting import error_detail
from ..workflows import hydrology
from .workflow_state import invalidate_meteo

# --- Input sources --------------------------------------------------------

def configure_input_adapters(dialog):
    """Wrap the DEM and pour-point combos in the layer-combo interface."""
    dialog._input_adapters = {
        "dem": InputComboAdapter(dialog.comboBox_demInput, "dem", dialog),
        "pour_points": InputComboAdapter(dialog.comboBox_pourPointInput, "pour_points", dialog),
    }


def input_layer_widgets(dialog):
    """Return persistent layer input widgets and their state keys."""
    return tuple(dialog._input_adapters.items())


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
            adapter.addItem(item.label, item.data)
        if include_files:
            for item in scan_project_inputs(dialog.project_directory or dialog.project_folder, kind):
                combo.addItem(item.label, item.data)
        index = _matching_input_index(combo, previous)
        if (
            index < 0
            and isinstance(previous, dict)
            and previous.get("origin") == "file"
            and previous.get("manual")
            and os.path.isfile(previous.get("path", ""))
        ):
            combo.addItem(previous.get("label") or previous["path"], previous)
            index = combo.count() - 1
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        if isinstance(previous, dict) and index < 0:
            adapter.layerChanged.emit(adapter.currentLayer())


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
        if candidate == identity or (origin == "qgis" and previous.get("source")
                                     and data.get("source") == previous["source"]):
            return index
    return -1


def browse_input_file(dialog, kind):
    """Add a manually selected file to one input box."""
    extensions = sorted(INPUT_EXTENSIONS[kind])
    path, _ = open_file(
        dialog,
        f"Select {kind.replace('_', ' ').title()} Input",
        f"Supported files ({' '.join(f'*{suffix}' for suffix in extensions)});;All files (*)",
    )
    if not path:
        return
    path = os.path.abspath(path)
    if os.path.splitext(path)[1].lower() not in INPUT_EXTENSIONS[kind]:
        QMessageBox.warning(
            dialog,
            "Unsupported Input",
            f"Select a {kind.replace('_', ' ')} file with one of these "
            f"extensions: {', '.join(extensions)}.",
        )
        return
    details = raster_details(dialog, path)
    if details is None:
        return
    add_file_input(dialog, kind, path, manual=True,
                   variable=details.variable, source_crs=details.source_crs)


def add_file_input(dialog, kind, path, *, manual, variable="", source_crs=""):
    """Select `path` in one input box, adding a file item when it is new."""
    combo = dialog._input_adapters[kind].combo_box
    for index in range(combo.count()):
        data = combo.itemData(index)
        if isinstance(data, dict) and data.get("path") == path:
            combo.setItemData(index, {**data, "variable": variable, "source_crs": source_crs})
            combo.setCurrentIndex(index)
            return
    label = portable_path(dialog.project_folder, path)
    combo.addItem(label, {
        "origin": "file", "kind": kind, "path": path, "label": label,
        "manual": manual, "variable": variable, "source_crs": source_crs,
    })
    combo.setCurrentIndex(combo.count() - 1)


def selected_input_file_paths(dialog):
    """Return local files already selected elsewhere in the plugin."""
    sources = [
        str(adapter.source_path() or "").split("|", 1)[0]
        for adapter in dialog._input_adapters.values()
    ]
    for configs in (
        dialog._categorical_lookup_configs,
        dialog._categorical_ready_configs,
        dialog._lai_input_config,
        {key: value.as_dict() if hasattr(value, "as_dict") else value
         for key, value in dialog._advanced_inputs.items()},
    ):
        sources.extend(_nested_strings(configs))
    sources.append(dialog._land_cover_ready_source)
    return {
        os.path.normcase(os.path.abspath(path))
        for path in sources
        if path and os.path.isfile(path)
    }


def _nested_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _nested_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _nested_strings(item)
    elif isinstance(value, str):
        yield value


def handle_model_input_changed(dialog, value=None):
    """Persist model inputs and invalidate a previously completed setup."""
    if dialog._loading_input_state:
        return
    from ...core.handlers.state import processing

    dialog.save_input_state()
    invalidate_meteo(dialog)
    if dialog.project_folder:
        processing.mark_workflow(dialog.project_folder, "execute_all_morphology", "pending")


# --- Outlets and domain definition ----------------------------------------

def populate_pour_point_outlet_fields(dialog, layer=None, preferred=None):
    """Populate the outlet ID field selector from the pour-point layer."""
    combo = dialog.comboBox_pourPointOutletID
    previous = str(preferred or combo.currentText() or "").strip()
    if layer is None:
        layer = dialog.input_combo("pour_points").currentLayer()
    names = list(layer.fields().names()) if layer is not None and layer.isValid() else []

    combo.blockSignals(True)
    combo.clear()
    combo.addItem("")
    combo.addItems(names)
    index = combo.findText(previous) if previous else -1
    for wanted in (previous, "station_id"):
        if index >= 0:
            break
        index = next(
            (candidate for candidate in range(1, combo.count())
             if combo.itemText(candidate).casefold() == wanted.casefold()),
            -1,
        )
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


def selected_outlet_id_field(dialog):
    return dialog.comboBox_pourPointOutletID.currentText().strip()


def selected_outlet_ids(dialog):
    """Return validated unique outlet IDs from the selected field."""
    field_name = selected_outlet_id_field(dialog)
    if not field_name:
        raise StationIdError("Select the pour-point outlet ID field.")
    return outlet_ids_from_layer(dialog.input_combo("pour_points").currentLayer(), field_name)


def update_gauged_outlet_count(dialog, layer=None):
    """Show configured gauges, falling back to the layer feature count."""
    from ...core.morphology.hydrology.outlets import (
        configured_gauged_outlet_ids,
        feature_count_text,
    )

    if layer is None:
        layer = dialog.input_combo("pour_points").currentLayer()
    configured_ids = configured_gauged_outlet_ids(dialog.project_folder)
    count = (
        str(len(configured_ids))
        if configured_ids is not None
        else feature_count_text(layer)
    )
    dialog.label_numberOfGaugedOutletsValue.setText(count)
    refresh_observation_stations(dialog)
    return count


def handle_domain_definition_type(dialog, index):
    """Dispatch the selected domain-definition workflow."""
    if dialog._loading_input_state:
        return
    combo = dialog.comboBox_domainDefinitionType
    previous = dialog._domain_definition_mode
    if int(index) == 1:
        dialog._domain_definition_mode = combo.currentText()
        dialog.save_input_state()
        open_domain_delineator(dialog)
        return
    if open_domain_assignment(dialog):
        dialog._domain_definition_mode = combo.currentText()
        dialog.save_input_state()
        return
    combo.blockSignals(True)
    combo.setCurrentIndex(combo.findText(previous) if previous else -1)
    combo.blockSignals(False)
    dialog.save_input_state()


def _selected_outlets(dialog, title):
    """Return the outlet IDs, or None after telling the user what is missing."""
    if not dialog.project_folder:
        QMessageBox.warning(dialog, title, "Select a project folder first.")
        return None
    try:
        outlet_ids = selected_outlet_ids(dialog)
    except StationIdError as error:
        QMessageBox.warning(dialog, title, str(error))
        return None
    if not outlet_ids:
        QMessageBox.warning(
            dialog, title, "The pour-point layer does not contain any outlet features."
        )
        return None
    return outlet_ids


def open_domain_assignment(dialog):
    """Open the non-interactive gauge assignment for the DEM-extent domain."""
    outlet_ids = _selected_outlets(dialog, "Domain Assignment")
    if outlet_ids is None:
        return False
    try:
        from ...core.handlers.state.domain_state import DOMAIN_MODE_DEM_EXTENT
        from ...core.handlers.state.nml_settings import sync_domain_settings
        from ...qgis_bridge.layers.domain import DomainWorkflow
        from ..dialogs.discharge_assignment import DischargeTableAssignmentDialog

        workflow = DomainWorkflow(
            dialog.project_folder,
            dialog.input_combo("pour_points").currentLayer(),
            selected_outlet_id_field(dialog),
            outlet_ids,
            prepare=lambda: hydrology.prepare_filled_dem(dialog),
            log=dialog.log_message,
        )
        state = workflow.load_synced_state(DOMAIN_MODE_DEM_EXTENT, True)
        assignment = DischargeTableAssignmentDialog(
            outlet_ids, dialog, initial_records=state.get("outlets", {}),
        )
        if assignment.exec() != QDialog.DialogCode.Accepted:
            return False
        workflow.apply_dem_extent(assignment.selected_assignments())
        sync_domain_settings(dialog.project_folder)
        update_gauged_outlet_count(dialog)
        invalidate_meteo(dialog)
        return True
    except Exception as error:
        dialog.log_message(f"ERROR: Domain assignment failed: {error}")
        dialog.log_message(f"Traceback: {traceback.format_exc()}")
        QMessageBox.critical(dialog, "Domain Assignment", str(error))
        return False


def open_domain_delineator(dialog):
    """Prepare the hydrology prerequisites, then open the delineation dialog."""
    outlet_ids = _selected_outlets(dialog, "Domain Delineator")
    if outlet_ids is None:
        return
    layer = dialog.input_combo("pour_points").currentLayer()
    field = selected_outlet_id_field(dialog)
    started = hydrology.start_domain_preflight(
        dialog,
        (dialog.comboBox_domainDefinitionType,),
        lambda context: _show_domain_delineator(dialog, context, layer, field, outlet_ids),
        lambda message: _domain_delineator_preflight_failed(dialog, message),
    )
    if not started:
        QMessageBox.information(
            dialog, "Domain Delineator", "Domain Delineator preparation is already running."
        )


def _show_domain_delineator(dialog, context, pour_points_layer, outlet_id_field, outlet_ids):
    from ...core.handlers.state.nml_settings import sync_domain_settings
    from ..dialogs.domain_delineator import DomainDelineatorDialog

    try:
        dialog._domain_delineator_dialog = DomainDelineatorDialog(
            dialog, pour_points_layer, outlet_id_field, outlet_ids, prepared_context=context,
        )
        dialog._domain_delineator_dialog.exec()
    except Exception as error:
        dialog.log_message(f"ERROR: Domain delineator failed: {error}")
        dialog.log_message(f"Traceback: {traceback.format_exc()}")
        QMessageBox.critical(dialog, "Domain Delineator", str(error))
    finally:
        dialog._domain_delineator_dialog = None
        try:
            sync_domain_settings(dialog.project_folder)
        except Exception as error:
            dialog.log_message(f"WARNING: Could not update domain namelist settings: {error}")
        dialog.save_input_state()
        update_gauged_outlet_count(dialog)


def _domain_delineator_preflight_failed(dialog, message):
    dialog.log_message(f"ERROR: Domain Delineator preparation failed: {message}")
    QMessageBox.critical(dialog, "Domain Delineator", error_detail(message))


# --- Result display -------------------------------------------------------

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


def configure_morphology_display(dialog):
    """Attach stable keys to the morphology display choices."""
    combo = dialog.comboBox_morphVariableToDisplay
    for index, key in enumerate(DISPLAY_KEYS[: combo.count()]):
        combo.setItemData(index, key)
    dialog.dateTimeEdit_forHistoricalInputs.setEnabled(False)


def refresh_morphology_date_control(dialog) -> None:
    """Enable the date selector only for historical land cover."""
    editor = dialog.dateTimeEdit_forHistoricalInputs
    periods = land_cover_periods(dialog.project_folder) if dialog.project_folder else []
    enabled = dialog.comboBox_morphVariableToDisplay.currentData() == "land_cover" and len(periods) > 1
    editor.setEnabled(enabled)
    if not enabled:
        return
    from ...core.handlers.lookup.manifests import period_datetime
    try:
        first = min(period_datetime(p.get("start_time") or str(p["start_year"])) for p in periods)
        last = max(period_datetime(p.get("end_time") or f'{p["end_year"]}-12-31T23:59:59') for p in periods)
    except (KeyError, TypeError, ValueError):
        editor.setEnabled(False)
        return
    editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
    editor.setDateTimeRange(QDateTime(first), QDateTime(last))
    editor.setDateTime(QDateTime(first))


def display_selected_morphology_layer(dialog) -> None:
    """Resolve the selected output and hand it to the QGIS renderer."""
    if not dialog.project_folder:
        QMessageBox.warning(dialog, "Display Layer", "Select a project folder first.")
        return
    key = dialog.comboBox_morphVariableToDisplay.currentData()
    if not key:
        QMessageBox.warning(dialog, "Display Layer", "Select a morphology layer.")
        return
    when = None
    if dialog.dateTimeEdit_forHistoricalInputs.isEnabled():
        when = dialog.dateTimeEdit_forHistoricalInputs.dateTime().toPyDateTime()
    output = resolve_display_output(dialog.project_folder, key, when=when)
    if output is None:
        QMessageBox.warning(
            dialog,
            "Display Layer",
            "The selected morphology output has not been prepared yet.",
        )
        return
    from ...qgis_bridge.display.morphology import show_output

    show_output(dialog, output)


def refresh_observation_stations(dialog):
    from ...core.post.graph import stations

    combo = dialog.comboBox_displayOutletStation
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

        if dialog._discharge_plot is None:
            dialog._discharge_plot = DischargePlotDialog(dialog)
        dialog._discharge_plot.show_series(combo.currentText(), timestamps, values)
    except (OSError, ValueError, ImportError) as error:
        dialog.log_message(f"ERROR: Discharge plot failed: {error}")
        QMessageBox.warning(dialog, "Discharge", str(error))


def on_tab_changed(dialog, index):
    """Refresh results when their tab is selected."""
    dialog.log_message(f"Switched to '{dialog.tabWidget_steps.tabText(index)}' tab.")
    # mHM reports nothing when it finishes, so the Outputs tab re-reads the
    # output folder each time it is shown.
    if dialog.tabWidget_steps.widget(index) is dialog.tab_outputs:
        from ...qgis_bridge.display import output as output_display

        output_display.refresh(dialog)
