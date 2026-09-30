"""What every workflow shares: the life of an Execute button, failures, outputs.

`start` is the one path from an Execute button to its finished state:

1. refuse while another workflow runs or a prerequisite is missing;
2. `build()` validates the form into a request, raising `ValueError`/`OSError`
   with the message the user needs;
3. mark the workflow running and lock the setup controls;
4. `submit(request, finished)` starts the work; the work calls
   `finished(ok, message)` once, on the GUI thread, which records the outcome
   and unlocks the controls.
"""
from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtWidgets import QMessageBox

from ...core.handlers.file.netcdf.raster import file_path
from ...core.handlers.store import registry
from ..controllers import workflow_state
from ..reporting import error_detail


def start(dialog, key, build, submit):
    label = workflow_state.specs(dialog)[key]["label"]
    if workflow_state.running_key(dialog) is not None:
        QMessageBox.information(dialog, label, "Another preprocessing task is currently running.")
        return False
    if not dialog.check_prerequisites():
        return False
    try:
        request = build()
    except (OSError, ValueError) as error:
        dialog.log_message(f"ERROR: Cannot start {label}: {error}")
        QMessageBox.warning(dialog, label, str(error))
        return False
    dialog.log_selected_input_paths(label)
    dialog.save_input_state()
    workflow_state.invalidate(dialog, "run_mhm")
    workflow_state.begin(dialog, key)

    def finished(ok, message=""):
        workflow_state.finish(dialog, key, ok, message)

    if not submit(request, finished):
        finished(False, f"{label} could not be started.")
        return False
    return True


def fail(dialog, label, message, failed=None):
    """Log a stage failure in full and hand its one useful line to `failed`."""
    dialog.log_message(f"ERROR: {label}: {message}")
    if failed is not None:
        failed(error_detail(message))


def register(dialog, path, name=None):
    """Record a prepared output in the project registry."""
    path = file_path(path)
    return registry.register(dialog.project_folder, path, name=name or Path(path).name)
