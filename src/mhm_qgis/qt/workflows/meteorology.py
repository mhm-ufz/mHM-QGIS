"""Meteorology workflow started by the ▶ of B. Meteorology in the project tree."""
from dataclasses import replace
from pathlib import Path

from qgis.PyQt.QtWidgets import QMessageBox

from ...core.executions import meteo
from ...core.grid import target_grid_from_header
from ...core.handlers.state.meteo_outputs import MeteorologyOutputState
from ...externals.mhm_tools_handler import NegativeAmounts
from ..controllers import forcing, grid, time
from . import lifecycle, post

WORKFLOW_KEY = "execute_all_meteo"
TASK_KEY = f"workflow-{WORKFLOW_KEY}"


def start(dialog):
    """Prepare every configured forcing variable on the common L2 grid."""
    coordinator = dialog.task_coordinator

    def build():
        if not coordinator.has_capacity():
            raise ValueError("All configured worker threads are currently busy.")
        metadata = grid.require_common_grid(dialog)
        target_grid = target_grid_from_header(metadata["l2_header"], dialog.selected_meteo_crs())
        target_grid.validate()
        return meteo.MeteorologyRun(
            setup_folder=Path(dialog.setup_folder),
            inputs=forcing.selected_meteo_specs(dialog),
            target_grid=target_grid,
            grid_metadata=dict(metadata),
            times=time.values(dialog),
        )

    def submit(run, finished):
        def log(message):  # Runs on the worker thread.
            coordinator.log.emit(TASK_KEY, message)

        state = MeteorologyOutputState(dialog.setup_folder, log=log)

        def prepare(task):
            # A worker thread must not raise a modal dialog: failures come back through
            # the coordinator, and small negative amounts as the result for `done` to ask.
            try:
                ok = meteo.prepare_forcing(
                    run, state, log=log, progress=task.setProgress, cancelled=task.isCanceled,
                )
            except NegativeAmounts as negative:
                return negative
            return bool(ok) and not task.isCanceled()

        def done(result):
            if not isinstance(result, NegativeAmounts):
                return finished(result)
            if QMessageBox.question(dialog, "Meteorology", f"{result}\n\nClip the negative values to 0 and "
                                    "continue?") != QMessageBox.StandardButton.Yes:
                return finished(False, f"{result} Fix the input, or run again and accept the clipping.")
            dialog._meteo_inputs[result.kind]["clip_negative"] = True
            dialog.save_input_state()
            clipped = replace(run, inputs=tuple(replace(spec, clip_negative=True) if spec.kind == result.kind
                                                else spec for spec in run.inputs))
            return submit(clipped, finished) or finished(False, "Meteorology preparation could not be restarted.")

        return coordinator.submit(
            TASK_KEY, "Meteorology Preparation", prepare,
            on_success=done,
            on_error=lambda message: finished(False, message),
            resource=post.RESOURCE,  # libnetcdf is not thread-safe: never beside a post job
        )

    return lifecycle.start(dialog, WORKFLOW_KEY, build, submit)
