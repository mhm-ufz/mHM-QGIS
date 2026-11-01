"""The Settings page's process cases: one combo per process, kept alike in the input state
and the saved namelists, and the inputs the cases need as rows under 2. Inputs."""
from __future__ import annotations

from qgis.PyQt.QtWidgets import QComboBox

from ... import intro
from ...core import datamodel
from ...core.handlers.state import nml_settings
from ...core.handlers.store.paths import namelist_project, schemas_folder
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


def current(dialog) -> dict:
    """Every process's case: the chosen or loaded ones over the schema defaults."""
    return {**datamodel.default_processes(selected_version(dialog)), **dialog._process_cases}


def restore(dialog, state) -> None:
    """On load: the saved namelists' cases, else the state's, else those chosen before."""
    dialog._process_cases = _saved(dialog) or dict(state.get("process_cases") or dialog._process_cases)
    show(dialog)


def show(dialog) -> None:
    """One combo per process of the selected version, on its current case."""
    form, version, cases = dialog.formLayout_processCases, selected_version(dialog), current(dialog)
    while form.rowCount():
        form.removeRow(0)
    for process, spec in datamodel.load_rules(version)["processes"].items():
        combo = QComboBox(dialog.page_settings)
        for case in spec.get("cases") or {}:
            combo.addItem(datamodel.case_label(version, process, case), int(case))
        if combo.findData(cases.get(process)) < 0 and process in cases:  # a case the rules don't list
            combo.addItem(datamodel.case_label(version, process, cases[process]), cases[process])
        combo.setCurrentIndex(max(0, combo.findData(cases.get(process))))
        combo.currentIndexChanged.connect(lambda _index, p=process, c=combo: changed(dialog, {p: c.currentData()}))
        form.addRow(process.replace("_", " "), combo)


def changed(dialog, cases) -> None:
    """Keep `cases` ({process: case}) in the input state and the namelists; show their inputs."""
    dialog._process_cases.update(cases)
    if dialog.project_folder:
        _sync(dialog)
    dialog.save_input_state()
    inputs(dialog)


def reread(dialog) -> None:
    """After Edit Namelists saved: the namelists' cases back into the state and the combos."""
    dialog._process_cases.update(_saved(dialog))
    dialog.save_input_state()
    show(dialog)
    inputs(dialog)


def _sync(dialog) -> None:
    """Write the cases into the saved namelists; before there are any, hand them to the first
    Edit Namelists through the one-shot `intro_values`."""
    from ...externals.nml_tools_handler import write_namelists

    version = selected_version(dialog)
    project = namelist_project(version, dialog._multi_domains is not None)
    try:
        saved = datamodel.read_namelists(dialog.project_folder)
    except (OSError, ValueError):
        saved = {}
    effects = {f"processes.{process}": case for process, case in current(dialog).items()}
    values = intro.handoff(version, effects, saved, project)
    if saved:
        try:
            write_namelists(dialog.project_folder, schemas_folder(version), values, project=project)
        except ValueError as error:
            dialog.log_message(f"ERROR: {error}")
        return
    merged = nml_settings.load_settings(dialog.project_folder).get("intro_values") or {}
    for profile, namelists in values.items():
        for name, fields in namelists.items():
            merged.setdefault(profile, {}).setdefault(name, {}).update(fields)
    nml_settings.update_section(dialog.project_folder, "intro_values", merged)


def inputs(dialog) -> None:
    """Show the rows under 2. Inputs the cases need, with what is prepared (✅)."""
    version = selected_version(dialog)
    rules, cases = datamodel.load_rules(version), current(dialog)
    try:
        findings = datamodel.check(dialog.project_folder, cases) if dialog.project_folder else None
    except (OSError, ValueError):
        findings = None
    if findings is None:  # no setup open: what the cases need, nothing checked yet
        findings = [datamodel.Finding(key, " or ".join(rules["inputs"][name].get("label", name) for name in key.split("|")),
                                      False, reasons, "")
                    for key, reasons in datamodel.required_inputs(version, cases).items()]
    project_tree.show_inputs(dialog, [f for f in findings if all(name in rules["inputs"] for name in f.input.split("|"))])
