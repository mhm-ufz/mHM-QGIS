"""mHM version, namelist editor, and model execution actions."""

import os
from pathlib import Path

from qgis.PyQt.QtCore import QSettings, QSignalBlocker
from qgis.PyQt.QtWidgets import QInputDialog, QMessageBox

from ...core import multi_domain
from ...core.handlers.state import processing
from ...core.handlers.store.layout import (
    ensure_multi_structure,
    ensure_project_structure,
)
from ...core.handlers.store.paths import (
    namelist_project,
    schemas_folder,
    workspace_folder,
)
from ...dependency_bootstrap import configure_qtpy_api
from ..dialogs.file_picker import open_file
from . import process_cases
from .categorical import refresh_advanced_nml_settings
from .time import restore as restore_simulation_times
from .time import values as simulation_times
from .version import selected as selected_version
from .workflow_state import (
    invalidate,
    require_active,
    running_key,
    set_button_state,
    set_status,
)

MHM_V6_SETTINGS_KEY = "mhm_qgis/executables/v6"
NEW_RUN = "<new>"


def populate_run_names(dialog, selected=None) -> None:
    """List named runs while retaining an entered name until it is first run."""
    from ...core.post import runs

    if selected is None:
        selected = getattr(dialog, "_selected_run_name", "")
    names = []
    if dialog.setup_folder:
        try:
            names = list(dict.fromkeys(
                ref.name for ref in runs.list_runs(dialog.setup_folder) if ref.name
            ))
        except (OSError, ValueError):
            names = []
    if selected and selected != NEW_RUN and selected not in names:
        names.insert(0, selected)
    combo = dialog.comboBox_runName
    with QSignalBlocker(combo):
        combo.clear()
        combo.addItems([*names, NEW_RUN])
        combo.setCurrentIndex(combo.findText(selected) if selected else -1)
    dialog._selected_run_name = selected if selected in names else ""


def select_run_name(dialog, index) -> bool:
    """Prompt when the user selects the `<new>` run entry."""
    combo = dialog.comboBox_runName
    if combo.itemText(index) != NEW_RUN:
        dialog._selected_run_name = combo.currentText().strip()
        return True
    previous = getattr(dialog, "_selected_run_name", "")
    name, accepted = QInputDialog.getText(dialog, "New run", "Run name:")
    name = name.strip()
    from ...core.post import runs

    if not accepted or not name or not runs.NAME.fullmatch(name):
        if accepted:
            QMessageBox.warning(
                dialog, "Run name", "Use letters, digits, '.', '_' or '-'."
            )
        populate_run_names(dialog, previous)
        return False
    populate_run_names(dialog, name)
    return True


def configured_v6_executable() -> str:
    return os.environ.get("MHM_QGIS_MHMV6") or str(
        QSettings().value(MHM_V6_SETTINGS_KEY, "") or ""
    )


def select_v6_executable(dialog):
    path, _ = open_file(dialog, "Select mHM v6 Executable", "Executable (*)")
    if not path:
        return None
    try:
        from ...externals.mhm_handler import executable

        path = str(executable("6", v6_path=path))
    except (OSError, ValueError) as error:
        QMessageBox.warning(dialog, "mHM v6 Executable", str(error))
        return None
    QSettings().setValue(MHM_V6_SETTINGS_KEY, path)
    invalidate(dialog, "run_mhm")
    return path


def require_v6_executable(dialog):
    path = configured_v6_executable()
    if path:
        try:
            from ...externals.mhm_handler import executable

            return str(executable("6", v6_path=path))
        except (OSError, ValueError) as error:
            dialog.log_message(f"WARNING: {error}")
            if os.environ.get("MHM_QGIS_MHMV6"):
                QMessageBox.warning(
                    dialog,
                    "mHM v6 Executable",
                    f"{error}\n\nCorrect or remove MHM_QGIS_MHMV6.",
                )
                return None
    return select_v6_executable(dialog)


def version_changed(dialog) -> None:
    if dialog.setup_folder:
        ensure_project_structure(dialog.setup_folder, selected_version(dialog))
        invalidate(dialog, "run_mhm")
        refresh_advanced_nml_settings(dialog)
    else:  # the version is fixed once a setup exists; cases chosen for the other one don't carry over
        dialog._process_cases.clear()
        process_cases.show(dialog)
    dialog.save_input_state()
    restore_simulation_times(dialog)
    dialog.groupBox_editCalibrationNamelists.setEnabled(
        selected_version(dialog).startswith("6")
    )


def _namelist_stamps(folder) -> dict:
    return {path.name: path.stat().st_mtime_ns for path in Path(folder).glob("*.nml")}


def edit_namelists(dialog, namelists=None, *, update_inputs=True) -> bool:
    """Open the namelist editor on the setup's nml-tools project (`v5`, `classic` or
    `combine`); `namelists` ({file profile: [namelist]}) narrows it to part of it."""
    if not require_active(dialog):
        return False
    try:
        from ...core.handlers.state import nml_settings
        from ...vSpecific import build_dimensions, build_initial_values

        version = selected_version(dialog)
        combined = multi_domain.is_multi(dialog.setup_folder)
        if update_inputs and not combined:
            nml_settings.update_section(
                dialog.setup_folder, "time", simulation_times(dialog)
            )
            nml_settings.sync_domain_settings(dialog.setup_folder)
        workspace = workspace_folder(dialog.setup_folder)
        schemas_dir = schemas_folder(version)
        if combined:
            ensure_multi_structure(dialog.setup_folder)
        else:
            ensure_project_structure(dialog.setup_folder, version)
        project = namelist_project(version, combined)
        if project == "combine" and namelists:
            # combine.nml (`meta`) holds what a domain's mhm.nml (`main`) would.
            namelists = {"meta" if name == "main" else name: groups for name, groups in namelists.items()}
        configure_qtpy_api()
        from ...externals.nml_tools_handler import launch_editor

        dialog.log_message(f"Opening the namelist editor for mHM {version} (project {project})...")
        if project == "v5":
            from ...externals.mhm_handler import name_mrm_group

            name_mrm_group(workspace, runnable=False)  # a run left mHM's group name; the schema reads _mrm
        before = _namelist_stamps(workspace)
        launch_editor(
            schemas_dir,
            workspace,
            project,
            namelists=namelists,
            initial_values=(
                build_initial_values(version, dialog) if update_inputs else None
            ),
            initial_dimensions=build_dimensions(version, dialog),
            log=dialog.log_message,
        )
        if _namelist_stamps(workspace) != before:
            if update_inputs and nml_settings.load_settings(dialog.setup_folder).get("intro_values"):
                # Handed over (only update_inputs passes them): now in the namelists; later edits win.
                nml_settings.update_section(dialog.setup_folder, "intro_values", {})
            process_cases.reread(dialog)
        return True
    except Exception as error:
        dialog.log_message(f"ERROR: Edit Namelists failed. Details: {error}")
        QMessageBox.critical(dialog, "Configuration Error", str(error))
        return False


def inputs_ready(dialog, setup_folder) -> bool:
    """Warn about inputs the saved processes read but nobody prepared, and about what mHM would
    stop on (`core.verify`); Yes runs anyway."""
    from ...core import datamodel, verify

    try:
        if multi_domain.is_multi(setup_folder):
            from ...core.handlers.state.nml_settings import load_settings

            stored = load_settings(setup_folder).get(multi_domain.SECTION, {}).get("domains", [])
            domains = multi_domain.resolve(setup_folder, stored)
            missing = [finding._replace(message=f"{domain.name}: {finding.message}")
                       for domain in domains for finding in datamodel.check(domain) if not finding.ok]
        else:
            domains = [Path(setup_folder)]
            missing = [finding for finding in datamodel.check(setup_folder) if not finding.ok]
    except ValueError as error:
        dialog.log_message(f"WARNING: Could not check the model inputs: {error}")
        return True
    try:
        problems = [f"{domain.name}: {problem}" if len(domains) > 1 else problem
                    for domain in domains for problem in verify.check(domain)]
    except (OSError, ValueError, KeyError, TypeError) as error:
        dialog.log_message(f"WARNING: Could not check the inputs against mHM: {error}")
        problems = []
    if not missing and not problems:
        return True
    for finding in missing:
        dialog.log_message(f"WARNING: {finding.message}")
    for problem in problems:
        dialog.log_message(f"ERROR: {problem}")
    answer = QMessageBox.question(
        dialog, "Model inputs",
        f"{len(missing)} input(s) the selected processes read look missing, and mHM would stop on "
        f"{len(problems)} check(s); the log lists them.\n\nRun mHM anyway?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def outputs_ready(dialog, setup_folder) -> bool:
    """Ask before a run that would write no output variable (every output switch off), or
    whose switched-on outputs mHM would drop because their process is off."""
    from ...core import datamodel

    try:
        switched_on = datamodel.outputs_on(setup_folder)
        dropped = datamodel.dropped_outputs(setup_folder) if switched_on else []
    except ValueError:
        return True
    if switched_on is None or (switched_on and not dropped):
        return True
    if not switched_on:
        title, text = "No outputs switched on", (
            "Every output is switched off in the output namelist, so mHM would write only the grid "
            "and the Outputs tab would have nothing to show. Turn outputs on in the namelist editor "
            "(or the introduction wizard).")
    else:
        for item in dropped:
            dialog.log_message(f"WARNING: mHM will drop output {item}.")
        title, text = "Outputs without their process", (
            f"mHM will drop {len(dropped)} switched-on output(s) because their process is off "
            f"(the log lists them): {', '.join(item.split(' ')[0] for item in dropped)}. Set the process "
            "cases (config_processes) in the namelist editor or the introduction wizard.")
    answer = QMessageBox.question(
        dialog, title, text + "\n\nRun mHM anyway?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
    return answer == QMessageBox.StandardButton.Yes


def browse_parameter_namelist(dialog):
    path, _ = open_file(dialog, "Parameter namelist", "Namelists (*.nml);;All files (*)")
    if path:
        dialog.lineEdit_pathParameterArgument.setText(path)


def run(dialog) -> bool:
    # while mHM or a workflow runs, a second run would overwrite the first one's outputs
    backup = dialog.groupBox_backupNamelists.isChecked()
    if running_key(dialog) or not require_active(dialog) or (
        backup and dialog.comboBox_runName.currentText().strip() in ("", NEW_RUN)
        and not select_run_name(dialog, dialog.comboBox_runName.findText(NEW_RUN))
    ):
        return False
    from ...core.post import runs

    setup_folder = dialog.setup_folder
    name = dialog.comboBox_runName.currentText().strip() if backup else ""

    def failed(message):
        processing.mark_workflow(setup_folder, "run_mhm", "failed", message)
        if dialog.setup_folder == setup_folder:
            set_button_state(dialog, "run_mhm", "failed")
        dialog.log_message(f"ERROR: {message}")

    def finished(exit_code):
        if exit_code == 0:
            processing.mark_workflow(setup_folder, "run_mhm", "completed")
            if dialog.setup_folder == setup_folder:
                from . import post_context

                set_button_state(dialog, "run_mhm", "completed")
                post_context.populate(dialog, rescan=True)  # output/ is the same ref with new files
            dialog.log_message("mHM completed successfully.")
            return
        failed(f"mHM exited with code {exit_code}.")

    try:
        from ...externals.mhm_handler import name_mrm_group
        from ...externals.mhm_handler import run as run_mhm

        version = selected_version(dialog)
        v6_path = require_v6_executable(dialog) if version.startswith("6") else None
        if version.startswith("6") and not v6_path:
            return False
        combined = multi_domain.is_multi(setup_folder)
        if combined:
            ensure_multi_structure(setup_folder)
            if not version.startswith("6"):  # a domain prepared again since Prepare: current inputs
                for note in multi_domain.combined_inputs(setup_folder):
                    dialog.log_message(f"Combined inputs: {note}")
        else:
            ensure_project_structure(setup_folder, version)
        if not inputs_ready(dialog, setup_folder) or not outputs_ready(dialog, setup_folder):
            return False
        parameter = None
        if dialog.checkBox_enableParameterArgument.isChecked():
            text = dialog.lineEdit_pathParameterArgument.text().strip()
            if not text:
                raise ValueError("Choose the parameter namelist to pass with -p.")
            parameter = Path(text).resolve()
            if not parameter.is_file():
                raise ValueError(f"Parameter namelist not found: {parameter}")
        if name and any(ref.name == name for ref in runs.list_runs(setup_folder)) and QMessageBox.question(
                dialog, "Run name", f"A backup named {name} exists. Overwrite its namelists?") \
                != QMessageBox.StandardButton.Yes:
            return False
        runs.link_outputs(setup_folder)
        if not version.startswith("6"):
            name_mrm_group(setup_folder, runnable=True)  # mHM v5's name; edit_namelists renames it back
        if name:
            runs.backup_namelists(setup_folder, name)
            dialog.log_message(f"Namelists backed up in runs/{name}/; mHM writes to output/.")
            populate_run_names(dialog, name)
        workspace = workspace_folder(setup_folder)
        terminal = dialog.open_project_terminal()
        if terminal is None:
            raise RuntimeError("Could not open the project terminal.")
        dialog.log_message(f"Running mHM in project directory: {workspace}")
        set_status(dialog, "run_mhm", "running")
        started = run_mhm(
            version,
            workspace,
            terminal.run_command,
            on_finished=finished,
            v6_path=v6_path,
            namelist="combine.nml" if combined and version.startswith("6") else None,
            parameter=parameter,
        )
        if not started:
            finished(-1)
        return started
    except (OSError, ValueError, RuntimeError) as error:
        set_status(dialog, "run_mhm", "failed", str(error))
        dialog.log_message(f"ERROR: Run mHM failed. Details: {error}")
        QMessageBox.critical(dialog, "mHM Execution Error", str(error))
        return False
