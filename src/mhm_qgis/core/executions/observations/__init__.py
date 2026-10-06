"""Prepare gridded observations with the shared L2 forcing pipeline."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ...grid import TargetGrid
from ...handlers.state.nml_outputs import register_observations
from ...handlers.store import registry
from ...handlers.store.paths import data_folder
from ...meteorology.forcing import (
    MeteoFolderSpec,
    SpatialMetadata,
    inspect_meteo_inputs_cached,
    process_meteo_inputs,
)


@dataclass(frozen=True)
class ObservationRun:
    project_folder: Path
    inputs: tuple[MeteoFolderSpec, ...]
    target_grid: TargetGrid
    inspections: Mapping[str, SpatialMetadata] | None = None


def prepare(run: ObservationRun, *, log=None, cancelled=None) -> bool:
    inspections = run.inspections or inspect_meteo_inputs_cached(
        run.project_folder, run.inputs, log=log, cancelled=cancelled)
    output_root = Path(data_folder(run.project_folder)) / "observation"
    result = process_meteo_inputs(
        run.inputs, output_root, run.target_grid,
        flat_layout=True, inspections=inspections, log=log, cancelled=cancelled)
    for path in result.outputs.values():
        registry.register(
            run.project_folder, path, name=Path(path).name,
            category="observation")
    register_observations(run.project_folder, result.outputs, run.inputs)
    return True


__all__ = ["ObservationRun", "prepare"]
