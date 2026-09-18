# -*- coding: utf-8 -*-
"""Intersect elevation bands with land-cover classes and write a report."""
from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ....externals import pyflwdir_handler
from ...grid import geotransform_bounds, raster_grids_match
from ...handlers import lookup
from .bands import (
    _dependencies,
    _read_raster,
    clean_output_name,
    collect_elevation_band_rasters,
    valid_raster_mask,
)

LogCallback = Callable[[str], None]


@dataclass(frozen=True)
class BandLandCoverResult:
    """CSV and optional aligned raster produced by the detail report."""

    report: Path
    row_count: int
    aligned_land_cover: Path | None = None


def _canonical_class(value: object) -> int | float:
    numeric = float(value)
    rounded = round(numeric)
    return int(rounded) if math.isclose(numeric, rounded, abs_tol=1e-9) else numeric


def _class_header(value: int | float, names: dict[int, str]) -> str:
    suffix = str(value) if isinstance(value, int) else f"{value:g}".replace("-", "minus_").replace(".", "_")
    value_suffix = clean_output_name(suffix, "unknown")
    name = names.get(value)
    if name is None:
        try:
            name = names.get(int(float(value)))
        except (TypeError, ValueError):
            pass
    if name:
        return f"land_cover_class_{value_suffix}_{clean_output_name(name, 'unknown')}_area"
    return f"land_cover_class_{value_suffix}_area"


def read_land_cover_class_names(
    config: dict[str, str] | None,
    *,
    log: LogCallback | None = None,
) -> dict[int, str]:
    """Read optional reporting labels from a land-cover lookup table."""
    if not config:
        return {}
    try:
        table = lookup.read(config["lookup_table"])
        fields = {lookup.normalize_key(column): column for column in table.columns}
        class_field = fields[lookup.normalize_key(config["class_field"])]
        name_field = next(
            (
                fields[candidate]
                for candidate in (
                    "classname",
                    "name",
                    "label",
                    "description",
                    "landcovername",
                    "type",
                )
                if candidate in fields
            ),
            None,
        )
        if name_field is None:
            return {}
        return {
            int(float(row[class_field])): str(row[name_field]).strip()
            for _, row in table.iterrows()
            if str(row[name_field]).strip()
        }
    except Exception as error:
        if log is not None:
            log(f"WARNING: Could not read land-cover class names: {error}")
        return {}


def _align_land_cover(
    input_path: str | Path,
    output_path: str | Path,
    reference: dict[str, Any],
) -> Path:
    _np, gdal = _dependencies()
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
    temporary.unlink(missing_ok=True)
    kwargs = {
        "format": "GTiff",
        "outputBounds": geotransform_bounds(
            reference["geotransform"], reference["cols"], reference["rows"]
        ),
        "width": int(reference["cols"]),
        "height": int(reference["rows"]),
        "resampleAlg": gdal.GRA_NearestNeighbour,
        "dstNodata": -9999,
        "outputType": gdal.GDT_Float32,
        "creationOptions": ["COMPRESS=LZW"],
    }
    if reference.get("projection"):
        kwargs["dstSRS"] = reference["projection"]
    dataset = gdal.Warp(
        str(temporary), str(input_path), options=gdal.WarpOptions(**kwargs)
    )
    if dataset is None:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Could not align land-cover raster: {input_path}")
    dataset = None
    os.replace(temporary, output)
    return output


def _parse_summary(path: str | Path) -> dict[tuple[str, int], dict[str, float | int | None]]:
    path = Path(path)
    if not path.is_file():
        return {}
    summary: dict[tuple[str, int], dict[str, float | int | None]] = {}
    with path.open("r", newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            subcatchment = str(row.get("subcatchment", "")).strip()
            try:
                band_id = int(float(row.get("band_id", "")))
            except (TypeError, ValueError):
                continue
            if subcatchment:
                summary[(subcatchment, band_id)] = {
                    "min_elevation": _optional_float(row.get("min_elevation")),
                    "max_elevation": _optional_float(row.get("max_elevation")),
                    "area_cells": _optional_int(row.get("area_cells")),
                    "area_m2": _optional_float(row.get("area_m2")),
                    "area_km2": _optional_float(row.get("area_km2")),
                }
    return summary


def _optional_float(value: object) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    try:
        return None if value in (None, "") else int(float(value))
    except (TypeError, ValueError):
        return None


def _subcatchment(path: str | Path) -> str:
    stem = Path(path).stem
    if stem.startswith("elevation_bands_"):
        stem = stem[len("elevation_bands_") :]
    return clean_output_name(stem, "subcatchment")


def create_band_land_cover_details(
    land_cover_path: str | Path,
    elevation_band_folder: str | Path,
    *,
    class_names: dict[int, str] | None = None,
    log: LogCallback | None = None,
) -> BandLandCoverResult:
    """Write area by elevation band and raw land-cover class."""
    np, _gdal = _dependencies()
    folder = Path(elevation_band_folder)
    band_paths = collect_elevation_band_rasters(folder)
    if not band_paths:
        raise FileNotFoundError("No elevation-band rasters were found.")

    reference = _read_raster(band_paths[0])
    land_cover = _read_raster(land_cover_path, as_float=True)
    aligned_path = None
    if not raster_grids_match(
        land_cover, reference, tolerance=1e-7, allow_missing_projection=True
    ):
        aligned_path = _align_land_cover(
            land_cover_path,
            folder / "land_cover_classes_aligned_to_elevation_bands.tif",
            reference,
        )
        if log is not None:
            log("Land-cover raster was aligned to the elevation-band grid.")
        land_cover = _read_raster(aligned_path, as_float=True)

    land_cover_array = land_cover["array"]
    land_cover_valid = valid_raster_mask(land_cover_array, land_cover["nodata"])
    band_references: list[tuple[str, dict[str, Any]]] = []
    class_values: set[int | float] = set()
    for band_path in band_paths:
        band = _read_raster(band_path)
        if (band["rows"], band["cols"]) != land_cover_array.shape:
            if log is not None:
                log(f"WARNING: Skipping band raster with mismatched shape: {band_path}")
            continue
        band_valid = valid_raster_mask(band["array"], band["nodata"])
        intersection = band_valid & (band["array"] > 0) & land_cover_valid
        class_values.update(_canonical_class(value) for value in np.unique(land_cover_array[intersection]))
        band_references.append((_subcatchment(band_path), band))

    if not band_references:
        raise ValueError("No elevation-band rasters matched the land-cover grid.")
    if not class_values:
        raise ValueError("No valid land-cover classes were found inside the elevation bands.")

    summary = _parse_summary(folder / "elevation_band_areas.csv")
    cell_area_m2 = pyflwdir_handler.cell_area_m2(reference)
    values = sorted(class_values, key=float)
    headers = [_class_header(value, class_names or {}) for value in values]
    fieldnames = [
        "subcatchment",
        "band_id",
        "min_elevation",
        "max_elevation",
        "area_cells",
        "area_m2",
        "area_km2",
        *headers,
    ]
    output_rows: list[dict[str, object]] = []
    for subcatchment, band in band_references:
        band_array = band["array"]
        band_valid = valid_raster_mask(band_array, band["nodata"]) & (band_array > 0)
        saved_ids = sorted(
            band_id for saved_subcatchment, band_id in summary if saved_subcatchment == subcatchment
        )
        raster_ids = (
            sorted(int(value) for value in np.unique(band_array[band_valid]))
            if np.any(band_valid)
            else []
        )
        for band_id in saved_ids or raster_ids:
            mask = band_valid & np.isclose(band_array, band_id)
            saved = summary.get((subcatchment, band_id), {})
            area_cells = saved.get("area_cells")
            area_cells = int(np.count_nonzero(mask)) if area_cells is None else int(area_cells)
            area_m2 = saved.get("area_m2")
            area_m2 = area_cells * cell_area_m2 if area_m2 is None else float(area_m2)
            area_km2 = saved.get("area_km2")
            area_km2 = area_m2 / 1_000_000.0 if area_km2 is None else float(area_km2)
            row: dict[str, object] = {
                "subcatchment": subcatchment,
                "band_id": band_id,
                "min_elevation": _format_optional(saved.get("min_elevation")),
                "max_elevation": _format_optional(saved.get("max_elevation")),
                "area_cells": area_cells,
                "area_m2": f"{area_m2:.6f}",
                "area_km2": f"{area_km2:.6f}",
            }
            land_cover_band = mask & land_cover_valid
            for value, header in zip(values, headers):
                class_cells = np.count_nonzero(
                    land_cover_band & np.isclose(land_cover_array, float(value))
                )
                row[header] = f"{int(class_cells) * cell_area_m2:.6f}"
            output_rows.append(row)

    if not output_rows:
        raise ValueError("No elevation-band land-cover detail rows were created.")
    output = folder / "elevation_band_land_cover_areas.csv"
    temporary = output.with_name(f".{output.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)
    os.replace(temporary, output)
    return BandLandCoverResult(output, len(output_rows), aligned_path)


def _format_optional(value: object) -> str:
    return "" if value is None else f"{float(value):.6f}"
