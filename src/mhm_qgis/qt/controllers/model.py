"""mHM version, namelist editor, and model execution actions."""

import os

from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import QMessageBox

from ...core.handlers.state import processing
from ...core.handlers.store.layout import ensure_project_structure
from ...core.handlers.store.paths import schemas_folder, workspace_folder
from ...dependency_bootstrap import configure_qtpy_api
from ..dialogs.file_picker import open_file
from .categorical import refresh_advanced_nml_settings
from .time import restore as restore_simulation_times
from .time import values as simulation_times
from .version import selected as selected_version
from .workflow_state import invalidate, require_active, set_button_state, set_status

MHM_V6_SETTINGS_KEY = "mhm_qgis/executables/v6"


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


def edit_namelists(dialog, file_profiles=None, *, update_inputs=True) -> bool:
    if not require_active(dialog):
        return False
    try:
        from ...core.handlers.state import nml_settings
        from ...vSpecific import build_dimensions, build_initial_values

        version = selected_version(dialog)
        if update_inputs:
            nml_settings.update_section(
                dialog.project_folder, "time", simulation_times(dialog)
            )
            nml_settings.sync_domain_settings(dialog.project_folder)
        workspace = workspace_folder(dialog.project_folder)
        schemas_dir = schemas_folder(version)
        ensure_project_structure(dialog.project_folder, version)
        configure_qtpy_api()
        from ...externals.nml_tools_handler import launch_profile_editor

        dialog.log_message(f"Opening namelist editor for mHM {version}...")
        launch_profile_editor(
            schemas_dir=schemas_dir,
            output_dir=workspace,
            initial_values=(
                build_initial_values(version, dialog) if update_inputs else None
            ),
            initial_dimensions=build_dimensions(version, dialog),
            file_profiles=file_profiles,
            log=dialog.log_message,
        )
        return True
    except Exception as error:
        dialog.log_message(f"ERROR: Edit Namelists failed. Details: {error}")
        QMessageBox.critical(dialog, "Configuration Error", str(error))
        return False


def run(dialog) -> bool:
    if not require_active(dialog):
        return False
    project_folder = dialog.project_folder

    def finished(exit_code):
        if exit_code == 0:
            processing.mark_workflow(project_folder, "run_mhm", "completed")
            if dialog.project_folder == project_folder:
                set_button_state(dialog, "run_mhm", "completed")
            dialog.log_message("mHM completed successfully.")
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
        ensure_project_structure(project_folder, version)
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
        )
        if not started:
            finished(-1)
        return started
    except (OSError, ValueError, RuntimeError) as error:
        set_status(dialog, "run_mhm", "failed", str(error))
        dialog.log_message(f"ERROR: Run mHM failed. Details: {error}")
        QMessageBox.critical(dialog, "mHM Execution Error", str(error))
        return False
