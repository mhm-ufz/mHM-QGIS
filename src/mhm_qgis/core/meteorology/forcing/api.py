"""Several forcing variables formatted by mhm-tools' `format_meteo_data`, staged and published together."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ....externals.mhm_tools_handler import (
    VARIABLE_LABELS,
    about_variable,
    format_meteo,
    output_periods,
    period_bounds,
    read_meteo_metadata,
)
from ...grid import assert_header_file_matches, write_header_file
from ...handlers.store.layout import meteo_file


@dataclass
class MeteoForcingResult:
    """Final files prepared for mHM."""

    outputs: dict[str, Path]
    headers: dict[str, Path]
    files_used: dict[str, int]


def _check_cancel(cancelled):
    if cancelled is not None and cancelled():
        raise RuntimeError("Meteorology task cancelled.")


def inspect_meteo_inputs(inputs):
    """Inspect one input per output variable."""
    specs = [spec.normalized() for spec in inputs]
    if len({spec.kind for spec in specs}) != len(specs):
        raise ValueError("Configure each meteorology variable only once.")
    inspected = {}
    for spec in specs:
        with about_variable(spec.kind):
            inspected[spec.kind] = read_meteo_metadata(spec)
    return inspected


def process_meteo_inputs(inputs, output_root, target_grid, *, flat_layout=False,
                         log=None, inspections=None, progress=None, cancelled=None):
    """Stream inputs onto L2, optionally aggregating to a coarser cadence."""
    target_grid.validate()
    specs = [spec.normalized() for spec in inputs]
    if not specs or len({spec.kind for spec in specs}) != len(specs):
        raise ValueError("Configure one input per meteorology variable.")
    inspected = inspections if inspections is not None else inspect_meteo_inputs(specs)
    import numpy as np
    windows = {
        spec.kind: (
            np.datetime64(inspected[spec.kind].start_time),
            period_bounds(output_periods(
                np.datetime64(inspected[spec.kind].end_time),
                spec.output_temporal_resolution))[1],
        ) for spec in specs
    }
    # every variable is written for the period they all cover; verify checks it spans the simulation
    common = (max(start for start, _ in windows.values()), min(end for _, end in windows.values()))
    if common[0] >= common[1]:
        raise ValueError("The meteorology variables share no common period: "
                         + ", ".join(f"{VARIABLE_LABELS.get(k, k)} {str(s)[:16]} to {str(e)[:16]}"
                                     for k, (s, e) in windows.items()))
    if log and len(set(windows.values())) > 1:
        log(f"Meteorology is written for the period all variables cover: {str(common[0])[:16]} to "
            f"{str(common[1])[:16]}.")
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    outputs, headers, files_used = {}, {}, {}
    tick = _file_ticker(specs, inspected, progress, log)
    with tempfile.TemporaryDirectory(prefix=".mhm_qgis-meteo-", dir=root) as temporary:
        stage = Path(temporary)
        grid_file = stage / "grid_header.txt"
        write_header_file(grid_file, target_grid.header)
        for spec in specs:
            with about_variable(spec.kind):
                _check_cancel(cancelled)
                metadata = inspected[spec.kind]
                output = stage / meteo_file(spec.kind, flat_layout)
                count = format_meteo(spec, output, grid_file, target_grid.crs,
                                     window=common, metadata=metadata,
                                     progress=tick, cancelled=cancelled, log=log)
            outputs[spec.kind] = output
            files_used[spec.kind] = len(metadata.files)
            if not flat_layout:
                header = output.with_name("header.txt")
                write_header_file(header, target_grid.header)
                assert_header_file_matches(header, target_grid.header)
                headers[spec.kind] = header
            if log:
                log(f"{spec.kind}: prepared {count} {spec.output_temporal_resolution.lower()} intervals.")
        _check_cancel(cancelled)
        return _publish_meteo_result(MeteoForcingResult(outputs, headers, files_used),
                                     root, flat_layout=flat_layout)


def _file_ticker(specs, inspected, progress, log):
    """Report percentage and a log line as input files are consumed.

    Without this the run is silent and pinned at 0% from the first line until a
    whole variable finishes, which on a multi-decade record is most of an hour.
    """
    total = sum(len(inspected[spec.kind].files) for spec in specs) or 1
    state = {"done": 0}

    def tick(kind):
        state["done"] += 1
        if progress is not None:
            progress(int(100 * state["done"] / total))
        if log and (state["done"] % 25 == 0 or state["done"] == total):
            log(f"{kind}: {state['done']} of {total} input files processed.")

    return tick


def _publish_meteo_result(
        staged: MeteoForcingResult,
        output_root: Path,
        *,
        flat_layout: bool = False) -> MeteoForcingResult:
    """Publish a complete staged result and roll back replacement failures."""
    first_output = next(iter(staged.outputs.values()))
    staging_root = first_output.parent if flat_layout else first_output.parents[1]
    backup_root = staging_root / "backups"
    managed_variables = tuple(staged.outputs)
    backups: list[tuple[Path, Path]] = []
    published: list[Path] = []

    try:
        for variable in managed_variables:
            final = output_root / meteo_file(variable, flat_layout)
            finals = (final,) if flat_layout else (final, final.with_name("header.txt"))
            for final in finals:
                if not final.exists():
                    continue
                backup = backup_root / final.relative_to(output_root)
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(final, backup)
                backups.append((backup, final))

        final_outputs = {}
        final_headers = {}
        for variable, staged_output in staged.outputs.items():
            final_output = output_root / meteo_file(variable, flat_layout)
            final_output.parent.mkdir(parents=True, exist_ok=True)
            final_header = None if flat_layout else final_output.with_name("header.txt")
            os.replace(staged_output, final_output)
            published.append(final_output)
            if final_header is not None and final_header not in published:  # strd shares ssrd's folder
                os.replace(staged.headers[variable], final_header)
                published.append(final_header)
                final_headers[variable] = final_header
            final_outputs[variable] = final_output

        return MeteoForcingResult(
            final_outputs,
            final_headers,
            dict(staged.files_used),
        )
    except Exception:
        for path in published:
            if path.exists():
                path.unlink()
        for backup, final in reversed(backups):
            if backup.exists():
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, final)
        raise
