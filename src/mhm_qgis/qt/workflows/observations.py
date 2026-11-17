"""Observation workflow started by the ▶ of C. Observations in the project tree."""
from pathlib import Path

from ...core.executions.observations import ObservationRun, prepare
from ...core.grid import target_grid_from_header
from ..controllers import forcing, grid
from . import lifecycle, post


def start(dialog):
    """Prepare every configured gridded observation on L1, the grid mHM reads them on."""

    def build():
        specs = forcing.observation_specs(dialog)
        if not specs:
            raise ValueError("Configure at least one additional observation.")
        grid.require_common_grid(dialog)
        return ObservationRun(
            Path(dialog.setup_folder),
            specs,
            target_grid_from_header(grid.grid_level_headers(dialog)["L1"], dialog.selected_meteo_crs()),
            inspections=forcing.observation_inspections(dialog) or None,
        )

    def submit(run, finished):
        return dialog.task_coordinator.submit(
            "execute-all-observations",
            "Prepare observations",
            lambda task: prepare(run, cancelled=task.isCanceled),
            on_success=lambda _result: finished(True),
            on_error=lambda message: finished(False, message),
            resource=post.RESOURCE,  # libnetcdf is not thread-safe: never beside a post job
        )

    return lifecycle.start(dialog, "execute_all_observations", build, submit)
