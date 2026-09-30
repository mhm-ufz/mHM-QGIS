"""Observation workflow started by ``pushButton_executeAllObservation``."""
from pathlib import Path

from ...core.executions.observations import ObservationRun, prepare
from ...core.grid import target_grid_from_header
from ..controllers import forcing, grid
from . import lifecycle


def start(dialog):
    """Prepare every configured gridded observation on the common L2 grid."""

    def build():
        specs = forcing.observation_specs(dialog)
        if not specs:
            raise ValueError("Configure at least one additional observation.")
        metadata = grid.require_common_grid(dialog)
        return ObservationRun(
            Path(dialog.project_folder),
            specs,
            target_grid_from_header(metadata["l2_header"], dialog.selected_meteo_crs()),
            inspections=forcing.observation_inspections(dialog) or None,
        )

    def submit(run, finished):
        return dialog.task_coordinator.submit(
            "execute-all-observations",
            "Prepare observations",
            lambda task: prepare(run, cancelled=task.isCanceled),
            on_success=lambda _result: finished(True),
            on_error=lambda message: finished(False, message),
            resource="morphology-files",
        )

    return lifecycle.start(dialog, "execute_all_observations", build, submit)
