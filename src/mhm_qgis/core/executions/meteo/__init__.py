"""Preparing mHM meteorology forcing.

Qt-free: the caller passes `log=` for the running commentary; a failure raises with the
message the user needs, so the same functions serve the worker thread and the tests.
"""
from __future__ import annotations

import traceback
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from ....externals.mhm_tools_handler import (
    VARIABLE_LABELS,
    MeteoFolderSpec,
    NegativeAmounts,
    SpatialMetadata,
    covers_end,
    validate_coverage,
)
from ...grid import TargetGrid
from ...handlers.state import processing
from ...handlers.store.layout import (
    expected_meteo_outputs,
    is_v6,
    meteo_mask_path,
    meteo_output_root,
)
from ...meteorology.forcing import (
    inspect_meteo_inputs_cached,
    preparation_fingerprint,
    process_meteo_inputs,
)
from ...meteorology.mask import write_meteo_mask
from ...meteorology.reuse import stale_meteo_variables


@dataclass(frozen=True)
class MeteorologyRun:
    """Immutable inputs prepared on the UI thread for one meteo run."""

    setup_folder: Path
    inputs: tuple[MeteoFolderSpec, ...]
    target_grid: TargetGrid
    grid_metadata: dict
    inspections: Mapping[str, SpatialMetadata] | None = None
    times: Mapping[str, str] | None = None


def _say(log, message):
    if log is not None:
        log(message)


def existing_outputs(setup_folder, state, *, log=None) -> list[str]:
    """Record the forcing files already on disk and return their variable names."""
    if not setup_folder:
        return []

    found = []
    for variable, paths in expected_meteo_outputs(setup_folder).items():
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
    if processing.section(run.setup_folder, "meteo_source_fingerprint") != digest:
        _say(log, "Meteorology input selection or files changed; preparing forcing.")
        return False
    stale = stale_meteo_variables(
        run.setup_folder,
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
         f"{meteo_output_root(run.setup_folder)} to force a rebuild.")
    processing.save_grid_metadata(run.setup_folder, run.grid_metadata)
    from ...handlers.state.nml_outputs import register_meteo
    expected = expected_meteo_outputs(run.setup_folder)
    register_meteo(
        run.setup_folder,
        {name: expected[name]["netcdf"] for name in variables},
        meteo_mask_path(run.setup_folder) if is_v6(run.setup_folder) else None,
        v6=is_v6(run.setup_folder),
    )
    return True


def main(run: MeteorologyRun, state, *, log=None, progress=None, cancelled=None) -> bool:
    """Entry point: prepare every selected meteorology forcing file; a failure raises its reason."""
    _say(log, "\n--- Preparing meteorology forcing files ---")
    _say(log, f"Meteorology output folder: {meteo_output_root(run.setup_folder)}")
    try:
        inspected = inspect_meteo_inputs_cached(
            run.setup_folder, run.inputs, log=log, cancelled=cancelled)
        run = replace(run, inspections=inspected)
        study = (run.grid_metadata or {}).get("study_bounds")
        for metadata in inspected.values() if study else ():
            validate_coverage(metadata, study, run.target_grid.crs, run.grid_metadata.get("l0_resolution", 0.0))
        if run.times:
            from ...handlers.state.nml_settings import validate_times
            times = validate_times(run.times, "v6" if is_v6(run.setup_folder) else "v5.13")
            cadences = {spec.kind: spec.output_temporal_resolution or spec.temporal_resolution for spec in run.inputs}
            for kind, metadata in inspected.items():
                if times["sim_start"] < metadata.start_time[:16] or not covers_end(
                        times["sim_end"], metadata.end_time, cadences[kind], is_v6(run.setup_folder)):
                    raise ValueError(
                        f"The simulation period is outside the {VARIABLE_LABELS.get(kind, kind).lower()} data "
                        f"({metadata.start_time} to {metadata.end_time}): shorten the simulation or extend the input.")
        if reuse_prepared(run, state, log=log):
            _save_period(run)
            return True
        flat = is_v6(run.setup_folder)
        result = process_meteo_inputs(
            inputs=run.inputs,
            output_root=meteo_output_root(run.setup_folder),
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
            mask = write_meteo_mask(meteo_mask_path(run.setup_folder), run.target_grid)
            state.mark_prepared(mask, mask.name)
            _say(log, f"Meteorology mask written: {mask}")
    except NegativeAmounts:
        raise  # not a failure yet: the user decides (qt/workflows/meteorology.py)
    except Exception as error:
        _say(log, f"ERROR: Meteorology processing failed: {error}\n{traceback.format_exc()}")
        raise

    for variable, output_path in result.outputs.items():
        state.mark_prepared(output_path, Path(output_path).name)
        header_path = result.headers.get(variable)
        if header_path:
            state.mark_prepared(header_path, Path(header_path).name)
    from ...handlers.state.nml_outputs import register_meteo
    register_meteo(run.setup_folder, result.outputs, mask, v6=is_v6(run.setup_folder))
    _say(log, "Meteorology forcing preparation completed.")
    _say(log, f"Prepared variables: {', '.join(sorted(result.outputs))}")
    processing.save_grid_metadata(run.setup_folder, run.grid_metadata)
    processing.overlay(run.setup_folder, {"meteo_source_fingerprint": _input_digest(run)})
    _save_period(run)
    return True


def _save_period(run):
    """Record the span every prepared variable covers."""
    metadata = (run.inspections or {}).values()
    if not metadata:
        return
    processing.overlay(run.setup_folder, {"meteo_period": {
        "start": max(item.start_time[:16] for item in metadata),
        "end": min(item.end_time[:16] for item in metadata),
    }})


def covers_period(setup_folder, times) -> bool:
    """Whether a simulation period lies inside the last prepared forcing."""
    period = processing.section(setup_folder, "meteo_period") or {}
    return bool(period) and (
        period["start"] <= times["sim_start"][:16] and times["sim_end"][:16] <= period["end"])


def _input_digest(run):
    """Grid checks alone cannot detect a changed glob, source or input file."""
    return [preparation_fingerprint(spec.normalized()) for spec in run.inputs]


__all__ = [
    "MeteorologyRun",
    "covers_period",
    "existing_outputs",
    "main",
    "reuse_prepared",
]
