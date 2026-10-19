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
from .categorical import refresh_advanced_nml_settings
from .time import restore as restore_simulation_times
from .time import values as simulation_times
from .version import selected as selected_version
from .workflow_state import invalidate, require_active, set_button_state, set_status

MHM_V6_SETTINGS_KEY = "mhm_qgis/executables/v6"
NEW_RUN = "<new>"


def populate_run_names(dialog, selected=None) -> None:
    """List named runs while retaining an entered name until it is first run."""
    from ...core.post import runs

    if selected is None:
        selected = getattr(dialog, "_selected_run_name", "")
    names = []
    if dialog.project_folder:
        try:
            names = list(dict.fromkeys(
                ref.name for ref in runs.list_runs(dialog.project_folder) if ref.name
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
    if dialog.project_folder:
        ensure_project_structure(dialog.project_folder, selected_version(dialog))
        invalidate(dialog, "run_mhm")
        refresh_advanced_nml_settings(dialog)
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
        combined = multi_domain.is_multi(dialog.project_folder)
        if update_inputs and not combined:
            nml_settings.update_section(
                dialog.project_folder, "time", simulation_times(dialog)
            )
            nml_settings.sync_domain_settings(dialog.project_folder)
        workspace = workspace_folder(dialog.project_folder)
        schemas_dir = schemas_folder(version)
        if combined:
            ensure_multi_structure(dialog.project_folder)
        else:
            ensure_project_structure(dialog.project_folder, version)
        project = namelist_project(version, combined)
        if project == "combine" and namelists:
            # combine.nml (`meta`) holds what a domain's mhm.nml (`main`) would.
            namelists = {"meta" if name == "main" else name: groups for name, groups in namelists.items()}
        configure_qtpy_api()
        from ...externals.nml_tools_handler import launch_editor

        dialog.log_message(f"Opening the namelist editor for mHM {version} (project {project})...")
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
        if _namelist_stamps(workspace) != before and nml_settings.load_settings(
                dialog.project_folder).get("intro_values"):
            # The wizard's choices are now in the saved namelists; later edits win.
            nml_settings.update_section(dialog.project_folder, "intro_values", {})
        return True
    except Exception as error:
        dialog.log_message(f"ERROR: Edit Namelists failed. Details: {error}")
        QMessageBox.critical(dialog, "Configuration Error", str(error))
        return False


def inputs_ready(dialog, project_folder) -> bool:
    """Warn about inputs the saved processes read but nobody prepared; Yes runs anyway."""
    from ...core import datamodel

    try:
        if multi_domain.is_multi(project_folder):
            from ...core.handlers.state.nml_settings import load_settings

            stored = load_settings(project_folder).get(multi_domain.SECTION, {}).get("domains", [])
            missing = [finding._replace(message=f"{domain.name}: {finding.message}")
                       for domain in multi_domain.resolve(project_folder, stored)
                       for finding in datamodel.check(domain) if not finding.ok]
        else:
            missing = [finding for finding in datamodel.check(project_folder) if not finding.ok]
    except ValueError as error:
        dialog.log_message(f"WARNING: Could not check the model inputs: {error}")
        return True
    if not missing:
        return True
    for finding in missing:
        dialog.log_message(f"WARNING: {finding.message}")
    answer = QMessageBox.question(
        dialog, "Model inputs missing",
        f"{len(missing)} input(s) the selected processes read look missing; the log lists them "
        "and what needs them.\n\nRun mHM anyway?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def outputs_ready(dialog, project_folder) -> bool:
    """Ask before a run that would write no output variable (every output switch off), or
    whose switched-on outputs mHM would drop because their process is off."""
    from ...core import datamodel

    try:
        switched_on = datamodel.outputs_on(project_folder)
        dropped = datamodel.dropped_outputs(project_folder) if switched_on else []
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


def run(dialog) -> bool:
    if not require_active(dialog) or (
        dialog.comboBox_runName.currentText().strip() == NEW_RUN
        and not select_run_name(dialog, dialog.comboBox_runName.currentIndex())
    ):
        return False
    from ...core.post import runs

    project_folder = dialog.project_folder
    name = dialog.comboBox_runName.currentText().strip()

    def finished(exit_code):
        if exit_code == 0:
            processing.mark_workflow(project_folder, "run_mhm", "completed")
            if dialog.project_folder == project_folder:
                set_button_state(dialog, "run_mhm", "completed")
            dialog.log_message("mHM completed successfully.")
            if name:
                runs.finish_named(project_folder, name)
                dialog.log_message(f"Run {name} is in runs/{name}/; the Outputs tab lists it.")
                if dialog.project_folder == project_folder:
                    from . import post_context

                    post_context.populate(dialog)
                    populate_run_names(dialog, name)
            return
        message = f"mHM exited with code {exit_code}."
        processing.mark_workflow(project_folder, "run_mhm", "failed", message)
        if dialog.project_folder == project_folder:
            set_button_state(dialog, "run_mhm", "failed")
        dialog.log_message(f"ERROR: {message}")

    try:
        from ...externals.mhm_handler import run as run_mhm

        version = selected_version(dialog)
        v6_path = require_v6_executable(dialog) if version.startswith("6") else None
        if version.startswith("6") and not v6_path:
            return False
        combined = multi_domain.is_multi(project_folder)
        if combined:
            ensure_multi_structure(project_folder)
        else:
            ensure_project_structure(project_folder, version)
        if not inputs_ready(dialog, project_folder) or not outputs_ready(dialog, project_folder):
            return False
        if name and any(ref.name == name for ref in runs.list_runs(project_folder)) and QMessageBox.question(
                dialog, "Run name", f"A run named {name} exists. Run again and overwrite its outputs?") \
                != QMessageBox.StandardButton.Yes:
            return False
        runs.link_outputs(project_folder, name)  # the namelists' output paths: runs/<name>/ or output/
        workspace = workspace_folder(project_folder)
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
        )
        if not started:
            finished(-1)
        return started
    except (OSError, ValueError, RuntimeError) as error:
        set_status(dialog, "run_mhm", "failed", str(error))
        dialog.log_message(f"ERROR: Run mHM failed. Details: {error}")
        QMessageBox.critical(dialog, "mHM Execution Error", str(error))
        return False
