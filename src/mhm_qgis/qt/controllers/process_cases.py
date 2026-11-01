"""The Settings page's process cases: one combo per process, the inputs the cases need as
rows under 2. Inputs, and the choices handed to Edit Namelists through `intro_values`."""
from __future__ import annotations

from qgis.PyQt.QtWidgets import QComboBox

from ... import intro
from ...core import datamodel
from ...core.handlers.state import nml_settings
from ...core.handlers.store.paths import namelist_project
from .. import project_tree
from .version import selected as selected_version


def _saved(dialog) -> dict:
    """The open setup's saved process cases ({} before its namelists exist)."""
    if not dialog.project_folder:
        return {}
    try:
        return datamodel.current_processes(dialog.project_folder) or {}
    except (OSError, ValueError):
        return {}


def show(dialog) -> None:
    """One combo per process of the selected version: "namelist value" (no change), then its cases."""
    form, version, saved = dialog.formLayout_processCases, selected_version(dialog), _saved(dialog)
    while form.rowCount():
        form.removeRow(0)
    for process, spec in datamodel.load_rules(version)["processes"].items():
        combo = QComboBox(dialog.page_settings)
        combo.addItem("namelist value", None)
        for case in spec.get("cases") or {}:
            combo.addItem(datamodel.case_label(version, process, case), int(case))
        current = dialog._process_cases.get(process, saved.get(process))
        combo.setCurrentIndex(0 if current is None else max(0, combo.findData(current)))
        combo.currentIndexChanged.connect(lambda _index, p=process, c=combo: changed(dialog, p, c.currentData()))
        form.addRow(process.replace("_", " "), combo)


def changed(dialog, process, case) -> None:
    if case is None:
        dialog._process_cases.pop(process, None)
    else:
        dialog._process_cases[process] = case
    inputs(dialog)
    if dialog.project_folder and dialog._process_cases:
        _hand_over(dialog)


def _hand_over(dialog) -> None:
    """Merge the chosen cases into the one-shot `intro_values` Edit Namelists opens with."""
    version = selected_version(dialog)
    try:
        saved = datamodel.read_namelists(dialog.project_folder)
    except (OSError, ValueError):
        saved = {}
    effects = {f"processes.{process}": case for process, case in dialog._process_cases.items()}
    values = intro.handoff(version, effects, saved, namelist_project(version, dialog._multi_domains is not None))
    merged = nml_settings.load_settings(dialog.project_folder).get("intro_values") or {}
    for profile, namelists in values.items():
        for name, fields in namelists.items():
            merged.setdefault(profile, {}).setdefault(name, {}).update(fields)
    nml_settings.update_section(dialog.project_folder, "intro_values", merged)


def inputs(dialog) -> None:
    """Show the rows under 2. Inputs the cases need, with what is prepared (✅)."""
    version = selected_version(dialog)
    rules, cases = datamodel.load_rules(version), {**_saved(dialog), **dialog._process_cases}
    try:
        findings = datamodel.check(dialog.project_folder, cases) if dialog.project_folder else None
    except (OSError, ValueError):
        findings = None
    if findings is None:  # no setup open: what the cases need, nothing checked yet
        findings = [datamodel.Finding(key, " or ".join(rules["inputs"][name].get("label", name) for name in key.split("|")),
                                      False, reasons, "")
                    for key, reasons in datamodel.required_inputs(version, cases).items()]
    project_tree.show_inputs(dialog, [f for f in findings if all(name in rules["inputs"] for name in f.input.split("|"))])
