"""Persist workflow state, reflect it on main-form buttons, and guard actions on it."""

from qgis.PyQt.QtWidgets import QMessageBox

from ...core.handlers.state import processing


def set_status(dialog, key, status, message=""):
    if dialog.project_folder:
        processing.mark_workflow(dialog.project_folder, key, status, message)
    set_button_state(dialog, key, status)


def invalidate(dialog, *keys):
    if dialog._loading_input_state or not dialog.project_folder:
        return
    for key in keys:
        processing.mark_workflow(dialog.project_folder, key, "pending")
        set_button_state(dialog, key, "")


def specs(dialog):
    return {
        "project_structure": {
            "button": dialog.pushButton_createProjectStructure,
            "label": "Project Structure",
            "completed_message": "Project structure is ready.",
            "failed_message": "Project structure setup failed.",
        },
        "scan_precipitation": {
            "button": dialog.pushButton_scanPrecipitationFiles,
            "label": "Precipitation Scan",
            "completed_message": "Precipitation scan completed.",
            "failed_message": "Precipitation scan failed.",
        },
        "execute_all_morphology": {
            "button": dialog.pushButton_executeAllMorphology,
            "label": "Execute All Morphology",
            "completed_message": "Morphology preparation completed successfully.",
            "failed_message": "Execute All Morphology failed. Check the log for details.",
        },
        "execute_all_meteo": {
            "button": dialog.pushButton_executeAllMeteo,
            "label": "Meteorology Preparation",
            "completed_message": "Meteorology preparation completed successfully.",
            "failed_message": "Meteorology preparation failed. Check the log for details.",
        },
        "execute_all_observations": {
            "button": dialog.pushButton_executeAllObservation,
            "label": "Observation Preparation",
            "completed_message": "Observation preparation completed.",
            "failed_message": "Observation preparation failed.",
        },
        "run_mhm": {
            "button": dialog.pushButton_execute_mHM,
            "label": "Run mHM",
            "completed_message": "mHM completed successfully.",
            "failed_message": "mHM execution failed.",
        },
    }


def button(dialog, workflow_key):
    return specs(dialog).get(workflow_key, {}).get("button")


def running_key(dialog):
    """Return the Execute workflow in progress, or None; at most one runs."""
    return dialog._running_workflow


def begin(dialog, workflow_key):
    """Mark `workflow_key` as the running workflow and lock the setup controls."""
    dialog._running_workflow = workflow_key
    set_status(dialog, workflow_key, "running")
    set_setup_controls_enabled(dialog, False)


def capture_button_styles(dialog):
    for workflow_key, spec in specs(dialog).items():
        dialog._workflow_button_default_styles[workflow_key] = spec["button"].styleSheet()


def set_setup_controls_enabled(dialog, enabled):
    """Lock the inputs a running workflow reads, so they cannot change under it."""
    for widget in (
        dialog.groupBox_mHMConfiguration,
        dialog.groupBox_ProjectFolder,
        dialog.groupBox_morphReferenceL0,
        dialog.groupBox_meteoReferenceL2,
        dialog.groupBox_4,
        dialog.tabWidget_steps,
    ):
        widget.setEnabled(enabled)


def set_button_state(dialog, workflow_key, status):
    control = button(dialog, workflow_key)
    if control is None:
        return
    if status == "running":
        control.setEnabled(False)
        color, border = "#f6c453", "#a66f00"
    else:
        control.setEnabled(True)
        if status == "completed":
            color, border = "#2e7d32", "#1b5e20"
        elif status == "failed":
            color, border = "#c62828", "#8e0000"
        else:
            control.setStyleSheet(
                dialog._workflow_button_default_styles.get(workflow_key, "")
            )
            return
    control.setStyleSheet(
        "QPushButton {"
        "text-align: left;"
        f"background-color: {color};"
        f"border: 1px solid {border};"
        "border-radius: 3px;"
        "}"
    )


def refresh_all(dialog):
    for workflow_key in specs(dialog):
        refresh(dialog, workflow_key)


def refresh(dialog, workflow_key):
    if running_key(dialog) == workflow_key:
        return
    status = processing.workflow(dialog.project_folder, workflow_key).get("status")
    set_button_state(
        dialog,
        workflow_key,
        status if status in {"completed", "failed"} else "",
    )


def finish(dialog, workflow_key, ok, message):
    """Record the outcome of the running workflow, unlock, and tell the user.

    Only the first outcome counts: a stage pipeline may report more than one.
    """
    if dialog._running_workflow != workflow_key:
        return
    dialog._running_workflow = None
    set_setup_controls_enabled(dialog, True)
    workflow = specs(dialog)[workflow_key]
    if ok:
        try:
            completed = workflow["completed_message"]
            processing.mark_workflow(dialog.project_folder, workflow_key, "completed", completed)
            set_button_state(dialog, workflow_key, "completed")
            dialog.log_message(completed)
            return
        except Exception as error:  # An unrecordable success is a failure.
            message = str(error)
    message = (
        message
        or processing.workflow(dialog.project_folder, workflow_key).get("message")
        or workflow["failed_message"]
    )
    processing.mark_workflow(dialog.project_folder, workflow_key, "failed", message)
    set_button_state(dialog, workflow_key, "failed")
    dialog.log_message(message)
    if not dialog._closing:
        QMessageBox.critical(dialog, workflow["label"], message)


def invalidate_meteo(dialog):
    if (
        dialog._loading_input_state
        or not dialog.project_folder
        or running_key(dialog) == "execute_all_meteo"
    ):
        return
    for key in ("execute_all_meteo", "run_mhm"):
        processing.remove_entry(dialog.project_folder, "workflows", key)
        set_button_state(dialog, key, "")


def switch_busy(dialog):
    terminal = dialog._terminal_dialog
    if (
        dialog.task_coordinator.is_busy()
        or running_key(dialog)
        or (terminal is not None and terminal.command_running())
    ):
        QMessageBox.warning(
            dialog,
            "Domain selection",
            "Finish or cancel running tasks before switching domains.",
        )
        return True
    return False


def require_active(dialog) -> bool:
    if dialog.project_folder:
        return True
    QMessageBox.warning(
        dialog,
        "Project Folder Required",
        "Select a project folder before editing namelists.",
    )
    return False
