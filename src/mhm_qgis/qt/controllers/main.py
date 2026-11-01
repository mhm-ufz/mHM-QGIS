"""Input sources, outlets, domain definition and result display on the main form."""
from __future__ import annotations

import os
import traceback

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QTableWidgetItem,
)

from ...core.handlers.store.morphology import resolve_display_output
from ...core.handlers.store.paths import portable_path
from ...core.morphology.hydrology.outlets import StationIdError, outlet_ids_from_layer
from ..dialogs.file_picker import open_file
from ..dialogs.input_selection import (
    INPUT_EXTENSIONS,
    InputComboAdapter,
    loaded_qgis_items,
    raster_details,
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
    """Populate input boxes from the layers loaded in QGIS."""
    for kind, adapter in dialog._input_adapters.items():
        combo = adapter.combo_box
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for item in loaded_qgis_items(kind):
            adapter.addItem(item.label, item.data)
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


DOMAIN_TYPES = ("Single Domain [DEM extent]", "Select Domains [using Domain Delineator]")


def radios(dialog, name, chosen):
    """Group the radios of `verticalLayout_<name>Types` (Designer); a click calls `chosen(text)`."""
    group, layout = QButtonGroup(dialog), getattr(dialog, f"verticalLayout_{name}Types")
    for index in range(layout.count()):
        group.addButton(layout.itemAt(index).widget())
    group.buttonClicked.connect(lambda button: chosen(button.text()))
    setattr(dialog, f"buttonGroup_{name}", group)


def check(dialog, name, text):
    """Check the radio for `text` (ignoring case, `_` as a space); none for another text."""
    group = getattr(dialog, f"buttonGroup_{name}")
    group.setExclusive(False)
    for button in group.buttons():
        button.setChecked(button.text().lower() == str(text or "").lower().replace("_", " "))
    group.setExclusive(True)


def embed(dialog, name, child):
    """Show the dialog `child` (or nothing) in `widget_<name>Form`, its OK/Cancel as one Set button.

    The form it replaces is closed silently: its own close handling runs, but not the
    `rejected`/`finished` slots that would show a form again.
    """
    layout = getattr(dialog, f"verticalLayout_{name}Form")
    while layout.count():
        old = layout.takeAt(0).widget()
        if old is not None:
            old.blockSignals(True)
            old.close()
            old.deleteLater()
    if child is not None:
        child.setWindowFlags(Qt.WindowType.Widget)
        box = child.findChild(QDialogButtonBox)
        if box is not None:
            box.setStandardButtons(QDialogButtonBox.StandardButton.NoButton)
            box.addButton("Set", QDialogButtonBox.ButtonRole.AcceptRole)
        layout.addWidget(child)
        child.show()


def handle_domain_definition_type(dialog, text):
    """Embed the chosen domain definition: the delineator, or the DEM-extent gauge assignment."""
    if text == DOMAIN_TYPES[1]:
        dialog._domain_definition_mode = text
        dialog.save_input_state()
        open_domain_delineator(dialog)
        return
    delineator = dialog.findChild(QDialog, "DomainDelineatorDialog")
    if delineator is not None and delineator._domain_jobs_busy():
        check(dialog, "domainDefinition", DOMAIN_TYPES[1])
        QMessageBox.information(dialog, "Domain Definition", "Wait for the active Domain Delineator task to finish.")
        return
    if not open_domain_assignment(dialog):
        check(dialog, "domainDefinition", dialog._domain_definition_mode)


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
    """Embed the gauge assignment for the DEM-extent domain; Set applies it. False without outlets."""
    outlet_ids = _selected_outlets(dialog, "Domain Assignment")
    if outlet_ids is None:
        return False
    try:
        from ...core.handlers.state.domain_state import DOMAIN_MODE_DEM_EXTENT
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
    except Exception as error:
        _assignment_failed(dialog, error)
        return False
    assignment.accepted.connect(lambda: _assign(dialog, workflow, assignment))
    embed(dialog, "domainDefinition", assignment)
    return True


def _assign(dialog, workflow, assignment):
    from ...core.handlers.state.nml_settings import sync_domain_settings

    try:
        workflow.apply_dem_extent(assignment.selected_assignments())
        sync_domain_settings(dialog.project_folder)
    except Exception as error:
        _assignment_failed(dialog, error)
        return
    dialog._domain_definition_mode = DOMAIN_TYPES[0]
    dialog.save_input_state()
    update_gauged_outlet_count(dialog)
    invalidate_meteo(dialog)
    open_domain_assignment(dialog)


def _assignment_failed(dialog, error):
    dialog.log_message(f"ERROR: Domain assignment failed: {error}")
    dialog.log_message(f"Traceback: {traceback.format_exc()}")
    QMessageBox.critical(dialog, "Domain Assignment", str(error))


def open_domain_delineator(dialog):
    """Prepare the hydrology prerequisites, then open the delineation dialog."""
    outlet_ids = _selected_outlets(dialog, "Domain Delineator")
    if outlet_ids is None:
        return
    layer = dialog.input_combo("pour_points").currentLayer()
    field = selected_outlet_id_field(dialog)
    started = hydrology.start_domain_preflight(
        dialog,
        (dialog.page_domainDefinition,),
        lambda context: _show_domain_delineator(dialog, context, layer, field, outlet_ids),
        lambda message: _domain_delineator_preflight_failed(dialog, message),
    )
    if not started:
        QMessageBox.information(
            dialog, "Domain Delineator", "Domain Delineator preparation is already running."
        )


def _show_domain_delineator(dialog, context, pour_points_layer, outlet_id_field, outlet_ids):
    """The delineator below the Domain Definition radios, until another type replaces it."""
    from ..dialogs.domain_delineator import DomainDelineatorDialog

    try:
        delineator = DomainDelineatorDialog(
            dialog, pour_points_layer, outlet_id_field, outlet_ids, prepared_context=context,
        )
    except Exception as error:
        dialog.log_message(f"ERROR: Domain delineator failed: {error}")
        dialog.log_message(f"Traceback: {traceback.format_exc()}")
        QMessageBox.critical(dialog, "Domain Delineator", str(error))
        return
    delineator.pushButton_close.hide()
    embed(dialog, "domainDefinition", delineator)


def _domain_delineator_preflight_failed(dialog, message):
    dialog.log_message(f"ERROR: Domain Delineator preparation failed: {message}")
    QMessageBox.critical(dialog, "Domain Delineator", error_detail(message))


# --- DEM derivatives ------------------------------------------------------

DEM_BUNDLE_VARIABLES = {"dem": "dem_filled", "slope": "slope", "aspect": "aspect", "fdir": "fdir", "facc": "facc"}


def show_dem_derivatives(dialog, variable=None):
    """The DEM Derivatives page: every derivative in dem_derivatives.nc, or only `variable`'s with its Replace row."""
    from ...core.handlers.file.netcdf.raster import describe
    from ...core.handlers.store.paths import temp_morphology_folder

    path = os.path.join(temp_morphology_folder(dialog.project_folder), "dem_derivatives.nc") \
        if dialog.project_folder else ""
    present, table = describe(path) if path else {}, dialog.tableWidget_demDerivatives
    table.setRowCount(len(DEM_BUNDLE_VARIABLES))
    for row, (key, name) in enumerate(DEM_BUNDLE_VARIABLES.items()):
        shape, units = present.get(name, ((), ""))
        file = os.path.relpath(path, dialog.project_folder) if name in present else "—"
        for column, text in enumerate((key, name, " \N{MULTIPLICATION SIGN} ".join(map(str, shape[-2:])) or "—", units, file)):
            table.setItem(row, column, QTableWidgetItem(text))
        table.setRowHidden(row, variable not in (None, key))
    for column in range(table.columnCount() - 1):  # the last one stretches
        table.resizeColumnToContents(column)
    dialog._dem_override = variable
    dialog.widget_demOverride.setVisible(variable is not None)
    dialog.widget_demOverride.setEnabled(DEM_BUNDLE_VARIABLES.get(variable) in present)


def browse_dem_override(dialog):
    path, _ = open_file(dialog, "Replacement raster", "Rasters (*.tif *.tiff *.nc *.asc);;All files (*)")
    if path:
        dialog.lineEdit_demOverride.setText(path)


def override_dem_derivative(dialog):
    """Replace the page's derivative with the chosen raster (same grid, whole DEM)."""
    from ...core.handlers.raster.tasks import override_derivative
    from ...core.handlers.store.paths import temp_morphology_folder
    from .. import project_tree

    path, variable = dialog.lineEdit_demOverride.text().strip(), dialog._dem_override
    try:
        override_derivative(temp_morphology_folder(dialog.project_folder), DEM_BUNDLE_VARIABLES[variable], path)
    except (OSError, RuntimeError, ValueError) as error:
        QMessageBox.warning(dialog, "Replace DEM Derivative", str(error))
        return
    dialog.log_message(f"Replaced {variable} in dem_derivatives.nc with {path}.")
    project_tree.refresh(dialog)


# --- Result display -------------------------------------------------------

def display_morphology(dialog, key) -> str:
    """Show the prepared output for a `resolve_display_output` key: its layer name, "" when there is none."""
    output = resolve_display_output(dialog.project_folder, key) if dialog.project_folder else None
    if output is None:
        return ""
    from ...qgis_bridge.display.morphology import show_output

    show_output(dialog, output)
    return output.name


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


def on_page_changed(dialog, page):
    """Refresh results when their page is shown."""
    dialog.log_message(f"Switched to '{dialog.tabWidget_steps.tabText(0)}'.")
    # mHM reports nothing when it finishes, so the Outputs page re-reads the
    # runs each time it is shown.
    if page is dialog.tab_outputs:
        from . import post_context

        post_context.populate(dialog)
