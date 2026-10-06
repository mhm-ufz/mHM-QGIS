"""Meteorology workflow started by ``pushButton_executeAllMeteo``."""
from pathlib import Path

from ...core.executions import meteo
from ...core.grid import target_grid_from_header
from ...core.handlers.state.meteo_outputs import MeteorologyOutputState
from ..controllers import forcing, grid, time
from . import lifecycle

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
            project_folder=Path(dialog.project_folder),
            inputs=forcing.selected_meteo_specs(dialog),
            target_grid=target_grid,
            grid_metadata=dict(metadata),
            times=time.values(dialog),
        )

    def submit(run, finished):
        def log(message):  # Runs on the worker thread.
            coordinator.log.emit(TASK_KEY, message)

        state = MeteorologyOutputState(dialog.project_folder, log=log)

        def prepare(task):
            # No `report`: a worker thread must not raise a modal dialog, so
            # failures come back through the coordinator for the GUI thread.
            ok = meteo.prepare_forcing(
                run, state, log=log, progress=task.setProgress, cancelled=task.isCanceled,
            )
            return bool(ok) and not task.isCanceled()

        return coordinator.submit(
            TASK_KEY, "Meteorology Preparation", prepare,
            on_success=finished,
            on_error=lambda message: finished(False, message),
        )

    return lifecycle.start(dialog, WORKFLOW_KEY, build, submit)
