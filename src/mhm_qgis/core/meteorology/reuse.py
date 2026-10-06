"""Reuse checks for meteorology forcing already recorded in the project state.

A prepared file may only be reused when the processing state records it, the
file and its header are still on disk, and that header still matches the L2
grid the current run targets. A changed L2 grid always forces a rebuild.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..grid import assert_header_file_matches, assert_netcdf_matches_header
from ..handlers.store.layout import expected_meteo_outputs
from ..handlers.store.registry import key_for

BASE_VARIABLES = ("pre", "tavg", "tmin", "tmax")


def required_meteo_variables(include_pet: bool) -> tuple[str, ...]:
    """Return the forcing variables one run has to produce."""
    if include_pet:
        return (*BASE_VARIABLES, "pet")
    return BASE_VARIABLES


def output_state_key(project_folder, path) -> str:
    """Return the processing-state key recorded for a meteorology output."""
    return key_for(project_folder, path)


def stale_meteo_variables(
        project_folder,
        recorded_outputs: Mapping[str, Any],
        header: Mapping[str, Any],
        variables: Iterable[str]) -> dict[str, str]:
    """Return the variables that still need preparing, mapped to the reason."""
    expected = expected_meteo_outputs(project_folder)
    stale: dict[str, str] = {}
    for variable in variables:
        paths = expected.get(variable)
        if paths is None:
            stale[variable] = "unknown meteorology variable"
            continue
        expected_paths = [paths["netcdf"]]
        if paths["header"] is not None:
            expected_paths.append(paths["header"])
        for path in expected_paths:
            key = output_state_key(project_folder, path)
            entry = recorded_outputs.get(key)
            if not isinstance(entry, Mapping) or not entry.get("exists"):
                stale[variable] = f"{path.name} is not recorded as prepared"
                break
            if not Path(path).is_file():
                stale[variable] = f"{path.name} is missing on disk"
                break
        else:
            if paths["header"] is None:
                # v6 has no header file, so the grid itself is the check.
                try:
                    assert_netcdf_matches_header(paths["netcdf"], variable, header)
                except (OSError, ValueError) as error:
                    stale[variable] = f"{error}"
                continue
            try:
                assert_header_file_matches(paths["header"], header)
            except (OSError, ValueError) as error:
                stale[variable] = f"{error}"
    return stale


__all__ = [
    "output_state_key",
    "required_meteo_variables",
    "stale_meteo_variables",
]
