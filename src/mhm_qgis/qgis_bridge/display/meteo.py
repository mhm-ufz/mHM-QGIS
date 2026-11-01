"""Canvas half of a meteorology row's eye: one time step of a prepared forcing variable,
which the time toolbar moves.
"""
from __future__ import annotations

from ...core.handlers.file.netcdf import grid_variables, read_slice
from ...core.meteorology.display import (
    meteo_output_path,
    meteo_steps,
    resolve_meteo_output,
)
from .canvas import show_dataarray


def show(dialog, variable, index=0):
    """Show step `index` of the prepared `variable`: (its steps, the layer name), or ((), "")."""
    steps = meteo_steps(meteo_output_path(dialog.project_folder, variable)) if dialog.project_folder else ()
    if not steps:
        return (), ""
    output = resolve_meteo_output(dialog.project_folder, variable, steps[max(0, min(index, len(steps) - 1))])
    # Same render path as the model-output display: one georeferenced slice.
    # Prepared forcing carries its own CRS, so none has to be supplied.
    grid = next((v for v in grid_variables(output.path) if v.name == output.variable), None)
    if grid is None:
        return (), ""
    show_dataarray(dialog, read_slice(output.path, grid, output.band - 1), output.name, when=output.when)
    return steps, output.name


__all__ = ["show"]
