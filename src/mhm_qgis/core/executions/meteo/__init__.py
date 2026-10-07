"""Preparing mHM meteorology forcing.

Qt-free: the caller passes `log=` for the running commentary and `report=` for
anything a user has to be shown, so the same functions serve the dialog, the
worker thread and the tests.
"""
from __future__ import annotations

import traceback
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from ...grid import TargetGrid
from ...handlers.state import processing
from ...handlers.store.layout import (
    expected_meteo_outputs,
    is_v6,
    meteo_mask_path,
    meteo_output_root,
)
from ...meteorology.forcing import (
    MeteoFolderSpec,
    SpatialMetadata,
    inspect_meteo_inputs_cached,
    preparation_fingerprint,
    process_meteo_inputs,
)
from ...meteorology.mask import write_meteo_mask
from ...meteorology.reuse import stale_meteo_variables
from ...utils.report import CRITICAL, INFORMATION


@dataclass(frozen=True)
class MeteorologyRun:
    """Immutable inputs prepared on the UI thread for one meteo run."""

    project_folder: Path
    inputs: tuple[MeteoFolderSpec, ...]
    target_grid: TargetGrid
    grid_metadata: dict
    inspections: Mapping[str, SpatialMetadata] | None = None
    times: Mapping[str, str] | None = None


def _say(log, message):
    if log is not None:
        log(message)


def existing_outputs(project_folder, state, *, log=None) -> list[str]:
    """Record the forcing files already on disk and return their variable names."""
    if not project_folder:
        return []

    found = []
    for variable, paths in expected_meteo_outputs(project_folder).items():
        header = paths["header"]
        if not paths["netcdf"].exists():
            continue
        if header is not None and not header.exists():
            continue
        state.mark_prepared(paths["netcdf"], paths["netcdf"].name)
        if header is not None:
            state.mark_prepared(header, header.name)
        found.append(variable)

    if found:
        _say(log, "Existing meteorology forcing files found: " + ", ".join(found))
    return found


def reuse_prepared(run: MeteorologyRun, state, *, log=None) -> bool:
    """Return True when recorded forcing already matches the target L2 grid."""
    variables = tuple(spec.normalized().kind for spec in run.inputs)
    digest = _input_digest(run)
    if processing.section(run.project_folder, "meteo_source_fingerprint") != digest:
        _say(log, "Meteorology input selection or files changed; preparing forcing.")
        return False
    stale = stale_meteo_variables(
        run.project_folder,
        state.load().get("outputs", {}),
        run.target_grid.header,
        variables,
    )
    if stale:
        for variable, reason in sorted(stale.items()):
            _say(log, f"{variable}: needs preparing ({reason}).")
        return False

    _say(log,
         "Meteorology forcing is already prepared on this L2 grid for "
         f"{', '.join(variables)}. Skipping meteorology preparation.")
    _say(log,
         "Change the L2 grid or delete the files under "
         f"{meteo_output_root(run.project_folder)} to force a rebuild.")
    processing.save_grid_metadata(run.project_folder, run.grid_metadata)
    from ...handlers.state.nml_outputs import register_meteo
    expected = expected_meteo_outputs(run.project_folder)
    register_meteo(
        run.project_folder,
        {name: expected[name]["netcdf"] for name in variables},
        meteo_mask_path(run.project_folder) if is_v6(run.project_folder) else None,
        v6=is_v6(run.project_folder),
    )
    return True


def prepare_forcing(run: MeteorologyRun, state, *, log=None, report=None,
                    progress=None, cancelled=None) -> bool:
    """Prepare every selected meteorology forcing file. Returns success."""
    _say(log, "\n--- Preparing meteorology forcing files ---")
    _say(log, f"Meteorology output folder: {meteo_output_root(run.project_folder)}")
    try:
        inspected = inspect_meteo_inputs_cached(
            run.project_folder, run.inputs, log=log, cancelled=cancelled)
        run = replace(run, inspections=inspected)
        if run.times:
            from ...handlers.state.nml_settings import validate_times
            times = validate_times(run.times, "v6" if is_v6(run.project_folder) else "v5.13")
            for kind, metadata in inspected.items():
                if times["sim_start"] < metadata.start_time[:16] or times["sim_end"] > metadata.end_time[:16]:
                    raise ValueError(f"Simulation period is outside {kind} coverage ({metadata.start_time} to {metadata.end_time}).")
        if reuse_prepared(run, state, log=log):
            _save_period(run)
            return True
        flat = is_v6(run.project_folder)
        result = process_meteo_inputs(
            inputs=run.inputs,
            output_root=meteo_output_root(run.project_folder),
            target_grid=run.target_grid,
            flat_layout=flat,
            log=log,
            inspections=run.inspections,
            progress=progress,
            cancelled=cancelled,
        )
        if cancelled is not None and cancelled():
            raise RuntimeError("Meteorology task cancelled.")
        mask = None
        if flat:
            # v6 names a meteorology mask in config_input.
            mask = write_meteo_mask(meteo_mask_path(run.project_folder), run.target_grid)
            state.mark_prepared(mask, mask.name)
            _say(log, f"Meteorology mask written: {mask}")
    except Exception as error:
        _say(log, f"ERROR: Meteorology processing failed: {error}\n{traceback.format_exc()}")
        if report is not None:
            report(CRITICAL, "Meteorology Error",
                   f"Meteorology processing failed.\n{error}")
        return False

    for variable, output_path in result.outputs.items():
        state.mark_prepared(output_path, Path(output_path).name)
        header_path = result.headers.get(variable)
        if header_path:
            state.mark_prepared(header_path, Path(header_path).name)
    from ...handlers.state.nml_outputs import register_meteo
    register_meteo(run.project_folder, result.outputs, mask, v6=is_v6(run.project_folder))
    _say(log, "Meteorology forcing preparation completed.")
    _say(log, f"Prepared variables: {', '.join(sorted(result.outputs))}")
    processing.save_grid_metadata(run.project_folder, run.grid_metadata)
    processing.overlay(run.project_folder, {"meteo_source_fingerprint": _input_digest(run)})
    _save_period(run)
    if report is not None:
        report(INFORMATION, "Success",
               "Meteorology forcing files prepared successfully.")
    return True


def _save_period(run):
    """Record the span every prepared variable covers."""
    metadata = (run.inspections or {}).values()
    if not metadata:
        return
    processing.overlay(run.project_folder, {"meteo_period": {
        "start": max(item.start_time[:16] for item in metadata),
        "end": min(item.end_time[:16] for item in metadata),
    }})


def covers_period(project_folder, times) -> bool:
    """Whether a simulation period lies inside the last prepared forcing."""
    period = processing.section(project_folder, "meteo_period") or {}
    return bool(period) and (
        period["start"] <= times["sim_start"][:16] and times["sim_end"][:16] <= period["end"])


def _input_digest(run):
    """Grid checks alone cannot detect a changed glob, source or input file."""
    return [preparation_fingerprint(spec.normalized()) for spec in run.inputs]


__all__ = [
    "MeteorologyRun",
    "covers_period",
    "existing_outputs",
    "prepare_forcing",
    "reuse_prepared",
]
