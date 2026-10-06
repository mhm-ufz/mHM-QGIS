"""Crop/pad an Arc/Info ASCII grid onto an aligned header by streaming text.

An ASCII grid is row-major
text, so padding is a line-by-line rewrite with constant memory instead.
"""
from __future__ import annotations

import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ....grid import aligned_window, headers_match, standardize_header


def read_ascii_header(path) -> dict:
    """Return the six header values of an Arc/Info ASCII grid."""
    header: dict[str, Any] = {}
    with open(path, encoding="utf-8") as stream:
        for _ in range(8):
            position = stream.tell()
            line = stream.readline()
            if not line:
                break
            parts = line.split()
            if len(parts) != 2:
                stream.seek(position)
                break
            key = parts[0].lower()
            if key in ("ncols", "nrows"):
                header[key] = int(float(parts[1]))
            elif key in (
                    "xllcorner", "yllcorner", "xllcenter", "yllcenter",
                    "cellsize", "nodata_value"):
                header[key] = float(parts[1])
            else:
                stream.seek(position)
                break
    try:
        return standardize_header(header)
    except ValueError as error:
        raise ValueError(f"Not a readable Arc/Info ASCII header: {path}") from error


def ascii_window_offsets(source: Mapping[str, Any],
                         target: Mapping[str, Any]) -> tuple[int, int]:
    """Return the (row, column) offset of the source inside the target grid.

    ``target index = source index + offset``. Raises when the grids do not share
    a cell size and cell boundaries, because padding would then shift the data.
    """
    try:
        window = aligned_window(source, target, label="The source grid")
    except ValueError as error:
        if "target grid resolution" in str(error):
            raise ValueError(
                f"Source cell size {source['cellsize']} does not match the target "
                f"cell size {target['cellsize']}; it cannot be padded without resampling."
            ) from error
        raise
    return (window["target_y"] - window["source_y"],
            window["target_x"] - window["source_x"])


def _format_header(target: Mapping[str, Any], nodata) -> str:
    return (
        f"ncols         {int(target['ncols'])}\n"
        f"nrows         {int(target['nrows'])}\n"
        f"xllcorner     {float(target['xllcorner'])}\n"
        f"yllcorner     {float(target['yllcorner'])}\n"
        f"cellsize      {float(target['cellsize'])}\n"
        f"NODATA_value  {nodata}\n"
    )


def pad_ascii_grid(path, target: Mapping[str, Any], nodata=-9999, pad=None, *, fill_nodata=False) -> Path:
    """Crop/pad an ASCII grid onto ``target`` in place, streaming row by row.

    Returns the path unchanged when the grid already matches the target. Cells
    outside the source extent become ``pad``, or ``nodata`` when no pad value
    is given. ``nodata`` stays the declared NODATA_value either way, so a class
    layer can be padded with a valid class without redefining its nodata.
    """
    path = Path(path)
    source = read_ascii_header(path)
    if headers_match(source, target) and not fill_nodata:
        return path

    window = aligned_window(source, target, label="The source grid")
    source_rows, source_columns = int(source["nrows"]), int(source["ncols"])
    target_rows, target_columns = int(target["nrows"]), int(target["ncols"])
    row_offset, column_offset = window["target_y"], window["target_x"]
    copy_rows, copy_columns = max(0, window["copy_rows"]), max(0, window["copy_cols"])
    if not copy_rows or not copy_columns:
        row_offset = copy_rows = column_offset = copy_columns = 0

    text = str(nodata if pad is None else pad)
    left = f"{text} " * column_offset
    right = f" {text}" * (target_columns - column_offset - copy_columns)
    blank = " ".join([text] * target_columns)
    temporary = path.with_name(f".{path.name}.pad.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with open(path, encoding="utf-8") as source_stream, \
                open(temporary, "w", encoding="utf-8") as output_stream:
            for _ in range(6):
                source_stream.readline()
            output_stream.write(_format_header(target, nodata))
            for _ in range(row_offset):
                output_stream.write(blank + "\n")
            written = 0
            for line in source_stream:
                values = line.split()
                if not values:
                    continue
                if len(values) != source_columns:
                    raise ValueError(f"{path.name} has a row with {len(values)} cells; expected {source_columns}.")
                if window["source_y"] <= written < window["source_y"] + copy_rows:
                    cells = values[window["source_x"]:window["source_x"] + copy_columns]
                    if fill_nodata:
                        cells = [text if not math.isfinite(float(value)) or float(value) == source.get("nodata_value", -9999)
                                 else value for value in cells]
                    cells = " ".join(cells)
                    output_stream.write(f"{left}{cells}{right}\n")
                written += 1
            if written != source_rows:
                raise ValueError(
                    f"{path.name} has {written} data row(s) but its header "
                    f"declares {source_rows}."
                )
            for _ in range(target_rows - row_offset - copy_rows):
                output_stream.write(blank + "\n")
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, path)
    return path


__all__ = [
    "ascii_window_offsets",
    "pad_ascii_grid",
    "read_ascii_header",
]
