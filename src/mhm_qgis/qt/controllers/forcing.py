"""Meteorology and gridded-observation variable inputs on the main form.

Each variable is described once in `meteo_var_details_input.ui`; only OK starts
a metadata scan, and a failed scan keeps the previous input. Every input must cover
the common extent Set on the Resolution page (`grid`); precipitation's page also
holds the simulation period.
"""
from __future__ import annotations

from dataclasses import asdict

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDialog, QMessageBox, QPushButton, QTableWidgetItem

from ...core import datamodel
from ...core.meteorology.forcing import inspect_meteo_folder_cached
from ...externals.mhm_tools_handler import (
    VARIABLES,
    MeteoFolderSpec,
    grid_note,
    normalize_unit,
    validate_coverage,
)
from .. import project_tree
from ..dialogs.meteo_var_details_input import MeteoVarDetailsDialog
from . import grid, main, process_cases
from .version import selected as selected_version
from .workflow_state import (
    invalidate,
    invalidate_meteo,
    require_active,
    switch_busy,
)


def edit_meteo_input(dialog, variable=None):
    """Accept metadata in a pop-up, then schedule only inspection."""
    variable = variable or dialog.comboBox_meteoVar.currentText()
    spec = _accepted_spec(dialog, variable, _meteo_initial(dialog, variable), "Meteorology input",
                          draft=f"meteo:{variable}")
    return None if spec is None else submit_meteo_input(dialog, variable, spec)


def page(variable):
    """The page that hosts `variable`'s form: precipitation has its own, with the period."""
    return "precipitation" if variable == "pre" else "meteoVar"


def show_meteo_input(dialog, variable):
    """A meteorology row's page: the variable's metadata form, filled in, with how its input meets
    the common grid; OK scans it."""
    key = f"meteo:{variable}"
    prompt = MeteoVarDetailsDialog(variable, dialog.project_directory, _meteo_initial(dialog, variable), dialog)
    main.draftable(dialog, prompt, key)
    prompt.accepted.connect(lambda: require_active(dialog) and not switch_busy(dialog) and submit_meteo_input(
        dialog, variable, prompt.spec))
    prompt.finished.connect(lambda _result: show_meteo_input(dialog, variable))
    prompt.label_description.setText(note(dialog, variable))
    main.embed(dialog, page(variable), prompt)
    project_tree.indicate(dialog, f"meteo_{variable}", prompt.label_setStatus)


def note(dialog, variable):
    """How the scanned input meets the common grid: coverage, then copy, shift or resampling."""
    metadata = dialog._meteo_inspections.get(variable)
    if metadata is None or not grid.grid_current(dialog):
        return "Set the resolutions, then Set this input to check it against the common grid."
    try:
        text = grid_note(metadata, dialog._grid_l2_metadata, dialog.selected_meteo_crs())
    except ValueError as error:
        return str(error)
    spec = dialog._meteo_inputs.get(variable, {})
    output = spec.get("output_temporal_resolution") or spec.get("temporal_resolution")
    if output and output != spec.get("temporal_resolution"):
        text += f" {spec['temporal_resolution']} → {output} ({(spec.get('aggregation') or '').lower()})."
    return text


def _initial(dialog, inputs, variable):
    """A variable's saved form values; the domain CRS until the file's CRS is chosen."""
    initial = dict(inputs.get(variable, {}))
    initial["crs"] = initial.get("crs") or dialog.selected_meteo_crs()
    return initial


def _meteo_initial(dialog, variable):
    """The saved input, overlaid by the values of a Set that has not scanned yet."""
    initial = _initial(dialog, dialog._meteo_inputs, variable)
    return {**initial, **dialog._drafts.get(f"meteo:{variable}", {})}


def _outside_period(dialog, variable, metadata, spec):
    """Why `metadata` does not span the saved simulation period, or None."""
    from ...core.handlers.state.nml_settings import load_settings
    from ...externals.mhm_tools_handler import covers_end

    period = load_settings(dialog.setup_folder).get("time") or {}
    start, end = period.get("sim_start", "")[:10], period.get("sim_end", "")[:10]
    if start and end and (metadata.start_time[:10] > start or not covers_end(
            period["sim_end"], metadata.end_time, spec.output_temporal_resolution or spec.temporal_resolution,
            selected_version(dialog).startswith("6"))):
        return (f"{variable} data cover {metadata.start_time[:10]} to {metadata.end_time[:10]}, "
                f"not the simulation period {start} to {end}.")
    return None


def submit_meteo_input(dialog, variable, spec):
    """Scan an accepted spec; a failed scan keeps the old input. The input must cover the saved
    Time Period, so that is Set first, and the common extent once that is Set."""
    from ...core.handlers.state.nml_settings import load_settings

    if not load_settings(dialog.setup_folder).get("time"):
        QMessageBox.warning(dialog, "Meteorology input", "Set the Time Period first: every forcing is checked against it.")
        return False

    def scanned(metadata):
        if error := _outside_period(dialog, variable, metadata, spec):
            failed(ValueError(error))
            return
        if not _covers_grid(dialog, metadata, failed):
            return
        dialog._meteo_inputs[variable] = _spec_state(spec)
        dialog._meteo_inspections[variable] = metadata
        dialog._drafts.pop(f"meteo:{variable}", None)
        if getattr(main.form_on(dialog, page(variable)), "draft_key", None) == f"meteo:{variable}":
            show_meteo_input(dialog, variable)  # rebuilt from the scanned input, with its note
        invalidate_meteo(dialog)
        populate_meteo_table(dialog)
        dialog.save_input_state()

    def failed(error):
        if not dialog._closing:
            QMessageBox.warning(dialog, "Meteorology scan", str(error))

    return _submit_scan(dialog, f"meteo-scan-{variable}", variable, spec, scanned, failed, (
        dialog.tabWidget_steps, dialog.pushButton_BrowseProjectFolder,
        dialog.comboBox_domainFolderSelector, dialog.pushButton_createOrLoadDomainStructure,
    ))


def _covers_grid(dialog, metadata, failed, level=None) -> bool:
    """Whether the scanned input covers the common extent once it is Set (else `failed` says why);
    with `level`, the log says how it reaches that grid."""
    if not grid.grid_current(dialog):
        return True
    saved = dialog._grid_l2_metadata
    try:
        validate_coverage(metadata, saved["study_bounds"], dialog.selected_meteo_crs(), saved["l0_resolution"])
        if level:
            dialog.log_message(f"{metadata.kind}: {grid_note(metadata, saved, dialog.selected_meteo_crs(), level)}")
    except ValueError as error:
        failed(error)
        return False
    return True


def edit_observation_input(dialog, variable=None):
    """Collect and inspect one gridded observation; it is prepared on L1."""
    variable = variable or dialog.comboBox_observationVar.currentText()
    spec = _accepted_spec(
        dialog, variable, _initial(dialog, dialog._observation_inputs, variable),
        "Observation input",
    )
    if spec is None:
        return None

    def scanned(metadata):
        if not _covers_grid(dialog, metadata, failed, "L1"):
            return
        dialog._observation_inputs[variable] = _spec_state(spec)
        dialog._observation_inspections[variable] = metadata
        invalidate(dialog, "execute_all_observations", "run_mhm")
        populate_observation_table(dialog)
        dialog.save_input_state()

    def failed(error):
        if not dialog._closing:
            QMessageBox.warning(dialog, "Observation scan", str(error))

    return _submit_scan(dialog, f"observation-scan-{variable}", variable, spec, scanned, failed, (
        dialog.tabWidget_steps, dialog.pushButton_BrowseProjectFolder,
    ))


def _accepted_spec(dialog, variable, initial, title, draft=None):
    """Return the accepted variable spec, or None when nothing should run; with `draft`, a
    failed scan keeps the entered values (see `main.draftable`)."""
    if not require_active(dialog) or switch_busy(dialog):
        return None
    if not variable:
        QMessageBox.warning(dialog, title, "Select a variable first.")
        return None
    prompt = MeteoVarDetailsDialog(variable, dialog.project_directory, initial, dialog)
    if draft:
        main.draftable(dialog, prompt, draft)
    if prompt.exec() != QDialog.DialogCode.Accepted:
        return None
    return prompt.spec


def _spec_state(spec):
    data = asdict(spec)
    data["folder"] = str(spec.folder)
    return data


def _submit_scan(dialog, key, variable, spec, scanned, failed, controls):
    return dialog.task_coordinator.submit(
        key, f"Scan {variable}",
        lambda task: inspect_meteo_folder_cached(
            dialog.setup_folder, spec, cancelled=task.isCanceled),
        controls=controls, on_success=scanned, on_error=failed,
        resource="morphology-files")


def populate_meteo_table(dialog):
    """Render one editable row per saved variable without rescanning."""
    _populate_forcing_table(
        dialog, dialog.tableWidget_meteoInputs, dialog._meteo_inputs, dialog._meteo_inspections, "L2",
        lambda key: edit_meteo_input(dialog, key))


def populate_observation_table(dialog):
    """Render configured gridded observations without rescanning."""
    _populate_forcing_table(
        dialog, dialog.tableWidget_observationVarInputs, dialog._observation_inputs,
        dialog._observation_inspections, "L1", lambda key: edit_observation_input(dialog, key))


def scan_details(dialog, variable, spec, metadata, level):
    """Files, Period, Cell Size, nRows, nCols, Extent, Accumulation, Unit Conversion and Regridding
    of a scanned input onto the `level` grid; "—" before its scan."""
    if metadata is None:
        return ("—",) * 9
    x, y = metadata.x_resolution, metadata.y_resolution
    output_unit = VARIABLES[variable][1]
    accumulation = (f"{spec.get('cumulative_resolution')}, reset "
                    f"{'last' if spec.get('cumulative_reset') is None else spec['cumulative_reset']}"
                    if spec.get("cumulative") else "None")
    if not grid.grid_current(dialog):
        regrid = "Set the resolutions first (Resolution page)."
    else:
        try:
            regrid = grid_note(metadata, dialog._grid_l2_metadata, dialog.selected_meteo_crs(), level)
        except ValueError as error:
            regrid = str(error)
    return (str(len(metadata.files)), f"{metadata.start_time[:10]} to {metadata.end_time[:10]}",
            f"{x:.6g}" if x == y else f"{x:.6g} x {y:.6g}", str(metadata.shape[0]), str(metadata.shape[1]),
            f"{', '.join(f'{value:.6g}' for value in metadata.bounds)} ({metadata.crs or 'no CRS'})",
            accumulation,
            "None" if normalize_unit(spec["unit"]) == output_unit else f"{spec['unit']} → {output_unit}",
            regrid)


def _populate_forcing_table(dialog, table, inputs, inspections, level, edit):
    table.setRowCount(len(inputs))
    for row, (variable, spec) in enumerate(inputs.items()):
        for column, text in enumerate((
                variable, spec["folder"], spec["pattern"],
                spec["temporal_resolution"],
                spec.get("output_temporal_resolution") or spec["temporal_resolution"],
                spec.get("aggregation") or "<None>", spec["unit"],
                *scan_details(dialog, variable, spec, inspections.get(variable), level))):
            item = QTableWidgetItem(str(text))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, column, item)
        button = QPushButton("Edit", table)
        # Dynamic Edit button → edit
        button.clicked.connect(lambda _checked=False, key=variable: edit(key))
        table.setCellWidget(row, table.columnCount() - 1, button)


def _spec(dialog, value):
    return MeteoFolderSpec(**dict(value, crs=value.get("crs") or dialog.selected_meteo_crs() or None)).normalized()


def selected_meteo_specs(dialog):
    """Run the configured variables the process cases read, with precipitation as the reference."""
    if "pre" not in dialog._meteo_inputs:
        raise ValueError("Configure and scan precipitation first.")
    needed = datamodel.meteo_variables(selected_version(dialog), process_cases.current(dialog))
    specs = tuple(_spec(dialog, value) for key, value in dialog._meteo_inputs.items() if key in needed)
    steps = {spec.output_temporal_resolution or spec.temporal_resolution for spec in specs}
    if not selected_version(dialog).startswith("6") and (len(steps) > 1 or steps - {"Daily", "Hourly"}):
        raise ValueError("mHM 5.13 reads every forcing at one time step, Daily or Hourly: "
                         f"set the output time step of all variables alike (now {', '.join(sorted(steps))}).")
    return specs


def observation_specs(dialog):
    """Return the configured gridded observations, ready for preparation."""
    return tuple(_spec(dialog, value) for value in dialog._observation_inputs.values())


def observation_inspections(dialog):
    """Return the scans already made for the configured observations."""
    return {
        key: dialog._observation_inspections[key]
        for key in dialog._observation_inputs
        if key in dialog._observation_inspections
    }
