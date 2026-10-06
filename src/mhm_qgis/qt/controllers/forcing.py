# -*- coding: utf-8 -*-
"""Meteorology and gridded-observation variable inputs on the main form.

Each variable is described once in `meteo_var_details_input.ui`; only OK starts
a metadata scan, and a failed scan keeps the previous input. Precipitation is
the reference variable whose scan establishes the common grid (`grid`).
"""
from __future__ import annotations

from dataclasses import asdict, replace

from qgis.PyQt.QtCore import QSignalBlocker, Qt
from qgis.PyQt.QtWidgets import QDialog, QMessageBox, QPushButton, QTableWidgetItem

from ...core.meteorology.forcing import MeteoFolderSpec, inspect_meteo_folder_cached
from ..dialogs.meteo_var_details_input import MeteoVarDetailsDialog
from . import grid
from .workflow_state import invalidate, invalidate_meteo, require_active, set_status, switch_busy


def scan_precipitation(dialog):
    """Precipitation establishes the grid after its metadata dialog accepts."""
    if dialog.check_prerequisites():
        edit_meteo_input(dialog, "pre")


def edit_meteo_input(dialog, variable=None):
    """Accept metadata, then schedule only inspection; failed scans keep old input."""
    variable = variable or dialog.comboBox_meteoVar.currentText()
    initial = dict(dialog._meteo_inputs.get(variable, {}))
    if variable == "pre":
        initial.update(folder=grid.precipitation_folder(dialog),
                       pattern=dialog.lineEdit_precipitationGlobDefinition.text())
    spec = _accepted_spec(dialog, variable, initial, "Meteorology input")
    if spec is None:
        return None
    if variable == "pre":
        set_status(dialog, "scan_precipitation", "running")

    def scanned(metadata):
        dialog._meteo_inputs[variable] = _spec_state(spec)
        dialog._meteo_inspections[variable] = metadata
        if variable == "pre":
            with QSignalBlocker(dialog.comboBox_precipitationFile):
                grid.select_precipitation_folder(dialog, str(spec.folder))
            with QSignalBlocker(dialog.lineEdit_precipitationGlobDefinition):
                dialog.lineEdit_precipitationGlobDefinition.setText(spec.pattern)
            grid.precipitation_scanned(dialog, metadata)
        invalidate_meteo(dialog)
        populate_meteo_table(dialog)
        dialog.save_input_state()

    def failed(error):
        if variable == "pre":
            set_status(dialog, "scan_precipitation", "failed", str(error))
        if not dialog._closing:
            QMessageBox.warning(dialog, "Meteorology scan", str(error))

    started = _submit_scan(dialog, f"meteo-scan-{variable}", variable, spec, scanned, failed, (
        dialog.tabWidget_steps, dialog.pushButton_BrowseProjectFolder,
        dialog.comboBox_domainFolderSelector, dialog.pushButton_createOrLoadDomainStructure,
    ))
    if variable == "pre" and not started:
        set_status(dialog, "scan_precipitation", "failed", "Scan is already running.")
    return started


def edit_observation_input(dialog, variable=None):
    """Collect and inspect one L2 gridded observation."""
    variable = variable or dialog.comboBox_observationVar.currentText()
    spec = _accepted_spec(
        dialog, variable, dict(dialog._observation_inputs.get(variable, {})),
        "Observation input",
    )
    if spec is None:
        return None

    def scanned(metadata):
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


def _accepted_spec(dialog, variable, initial, title):
    """Return the accepted variable spec, or None when nothing should run."""
    if not require_active(dialog) or switch_busy(dialog):
        return None
    if not variable:
        QMessageBox.warning(dialog, title, "Select a variable first.")
        return None
    prompt = MeteoVarDetailsDialog(variable, dialog.project_directory, initial, dialog)
    if prompt.exec() != QDialog.DialogCode.Accepted:
        return None
    return replace(prompt.spec, crs=dialog.selected_meteo_crs() or None)


def _spec_state(spec):
    data = asdict(spec)
    data["folder"] = str(spec.folder)
    return data


def _submit_scan(dialog, key, variable, spec, scanned, failed, controls):
    return dialog.task_coordinator.submit(
        key, f"Scan {variable}",
        lambda task: inspect_meteo_folder_cached(
            dialog.project_folder, spec, cancelled=task.isCanceled),
        controls=controls, on_success=scanned, on_error=failed,
        resource="morphology-files")


def populate_meteo_table(dialog):
    """Render one editable row per saved variable without rescanning."""
    _populate_forcing_table(
        dialog.tableWidget_meteoInputs, dialog._meteo_inputs,
        lambda key: edit_meteo_input(dialog, key))


def populate_observation_table(dialog):
    """Render configured gridded observations without rescanning."""
    _populate_forcing_table(
        dialog.tableWidget_observationVarInputs, dialog._observation_inputs,
        lambda key: edit_observation_input(dialog, key))


def _populate_forcing_table(table, inputs, edit):
    table.setRowCount(len(inputs))
    for row, (variable, spec) in enumerate(inputs.items()):
        for column, text in enumerate((
                variable, spec["folder"], spec["pattern"],
                spec["temporal_resolution"],
                spec.get("output_temporal_resolution") or spec["temporal_resolution"],
                spec.get("aggregation") or "<None>", spec["unit"])):
            item = QTableWidgetItem(str(text))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, column, item)
        button = QPushButton("Edit", table)
        # Dynamic Edit button → edit
        button.clicked.connect(lambda _checked=False, key=variable: edit(key))
        table.setCellWidget(row, 7, button)


def _spec(dialog, value):
    return MeteoFolderSpec(**dict(value, crs=dialog.selected_meteo_crs() or None)).normalized()


def selected_meteo_specs(dialog):
    """Run the configured variable collection, with precipitation as the reference."""
    if "pre" not in dialog._meteo_inputs:
        raise ValueError("Configure and scan precipitation first.")
    return tuple(
        grid.precipitation_spec(dialog) if key == "pre" else _spec(dialog, value)
        for key, value in dialog._meteo_inputs.items()
    )


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
