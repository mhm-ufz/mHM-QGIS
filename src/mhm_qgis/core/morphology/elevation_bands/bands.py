"""Create elevation-band rasters and their area summary."""
from __future__ import annotations

import csv
import math
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ....externals import pyflwdir_handler

LogCallback = Callable[[str], None]


@dataclass(frozen=True)
class ElevationBandResult:
    """Files and range produced by one elevation-band run."""

    rasters: tuple[Path, ...]
    summary: Path
    minimum: float
    maximum: float
    count: int


def _dependencies():
    try:
        import numpy as np
        from osgeo import gdal
    except ImportError as error:
        raise RuntimeError(
            "Elevation-band processing requires NumPy and GDAL Python bindings."
        ) from error
    return np, gdal


def clean_output_name(value: object, fallback: str) -> str:
    """Return a filesystem-safe short name."""
    cleaned = "".join(
        character
        for character in str(value)
        if character.isalnum() or character in (" ", "-", "_")
    ).rstrip()
    return cleaned.replace(" ", "_") or fallback


def nice_step(raw_step: float) -> float:
    """Round an interval up to a 1/2/5/10-style step."""
    if raw_step <= 0:
        return 1.0
    exponent = math.floor(math.log10(raw_step))
    base = 10**exponent
    return float(next(value * base for value in (1, 2, 5, 10) if value * base >= raw_step))


def elevation_range_from_window_width(
    minimum: float,
    maximum: float,
    window_width: float,
) -> tuple[float, float, int] | None:
    """Return an outward-rounded elevation range for a fixed band width."""
    if window_width <= 0:
        return None
    if maximum <= minimum:
        maximum = minimum + window_width

    rounded_minimum = math.floor(minimum / window_width) * window_width
    rounded_maximum = math.ceil(maximum / window_width) * window_width
    if rounded_maximum <= rounded_minimum:
        rounded_maximum = rounded_minimum + window_width
    count = int(
        math.ceil(((rounded_maximum - rounded_minimum) / window_width) - 1e-12)
    )
    return rounded_minimum, rounded_minimum + count * window_width, count


def valid_raster_mask(array, nodata):
    """Return true for finite raster values different from nodata."""
    np, _gdal = _dependencies()
    valid = np.isfinite(array)
    if nodata is not None and np.isfinite(nodata):
        valid &= ~np.isclose(array, nodata)
    return valid


def collect_watershed_rasters(folder: str | Path) -> tuple[Path, ...]:
    """Return final per-subcatchment watershed rasters in stable order."""
    folder = Path(folder)
    if not folder.is_dir():
        return ()
    return tuple(
        path
        for path in sorted(folder.iterdir())
        if path.is_file()
        and path.name.lower().startswith("4_watershed_")
        and path.suffix.lower() == ".tif"
        and not path.name.lower().endswith("_raw.tif")
    )


def collect_elevation_band_rasters(folder: str | Path) -> tuple[Path, ...]:
    """Return per-subcatchment elevation-band rasters in stable order."""
    folder = Path(folder)
    if not folder.is_dir():
        return ()
    return tuple(
        path
        for path in sorted(folder.iterdir())
        if path.is_file()
        and path.name.lower().startswith("elevation_bands_")
        and path.suffix.lower() == ".tif"
    )


def _read_raster(path: str | Path, *, as_float: bool = False) -> dict[str, Any]:
    np, gdal = _dependencies()
    dataset = gdal.Open(str(path))
    if dataset is None:
        raise RuntimeError(f"Could not open raster: {path}")
    band = dataset.GetRasterBand(1)
    array = band.ReadAsArray()
    if array is None:
        raise RuntimeError(f"Could not read raster values: {path}")
    if as_float:
        array = array.astype(np.float32)
    reference = {
        "array": array,
        "nodata": band.GetNoDataValue(),
        "geotransform": dataset.GetGeoTransform(),
        "projection": dataset.GetProjection(),
        "rows": dataset.RasterYSize,
        "cols": dataset.RasterXSize,
    }
    band = None
    dataset = None
    return reference


def _write_raster(
    path: str | Path,
    array,
    reference: dict[str, Any],
    *,
    nodata: float | int,
    data_type,
) -> Path:
    _np, gdal = _dependencies()
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
    temporary.unlink(missing_ok=True)
    dataset = gdal.GetDriverByName("GTiff").Create(
        str(temporary),
        int(reference["cols"]),
        int(reference["rows"]),
        1,
        data_type,
        options=["COMPRESS=LZW"],
    )
    if dataset is None:
        raise RuntimeError(f"Could not create raster: {output}")
    try:
        dataset.SetGeoTransform(reference["geotransform"])
        if reference.get("projection"):
            dataset.SetProjection(reference["projection"])
        band = dataset.GetRasterBand(1)
        band.SetNoDataValue(float(nodata))
        band.WriteArray(array)
        band.FlushCache()
        band = None
        dataset = None
        os.replace(temporary, output)
    except Exception:
        dataset = None
        temporary.unlink(missing_ok=True)
        raise
    return output


def raster_value_range(path: str | Path) -> tuple[float, float]:
    """Return the minimum and maximum valid values in a raster."""
    np, _gdal = _dependencies()
    reference = _read_raster(path, as_float=True)
    valid = valid_raster_mask(reference["array"], reference["nodata"])
    if not np.any(valid):
        raise ValueError(f"Raster has no valid values: {path}")
    values = reference["array"][valid]
    return float(np.nanmin(values)), float(np.nanmax(values))


def _subcatchment_name(path: str | Path, fallback: str) -> str:
    name = Path(path).stem
    if name.startswith("4_watershed_"):
        name = name[len("4_watershed_") :]
    return clean_output_name(name, fallback)


def _write_summary(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = (
        "subcatchment",
        "band_id",
        "min_elevation",
        "max_elevation",
        "area_cells",
        "area_m2",
        "area_km2",
    )
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "min_elevation": f"{float(row['min_elevation']):.6f}",
                    "max_elevation": f"{float(row['max_elevation']):.6f}",
                    "area_m2": f"{float(row['area_m2']):.6f}",
                    "area_km2": f"{float(row['area_km2']):.6f}",
                }
            )
    os.replace(temporary, path)


def create_elevation_bands(
    dem_path: str | Path,
    watershed_paths: Iterable[str | Path],
    output_folder: str | Path,
    window_width: float,
    *,
    log: LogCallback | None = None,
) -> ElevationBandResult:
    """Create one classified elevation raster per watershed and an area CSV."""
    np, gdal = _dependencies()
    dem = _read_raster(dem_path, as_float=True)
    dem_array = dem["array"]
    dem_valid = valid_raster_mask(dem_array, dem["nodata"])
    if not np.any(dem_valid):
        raise ValueError("The filled DEM has no valid elevation values.")

    elevation_range = elevation_range_from_window_width(
        float(np.nanmin(dem_array[dem_valid])),
        float(np.nanmax(dem_array[dem_valid])),
        float(window_width),
    )
    if elevation_range is None:
        raise ValueError("Elevation window width must be greater than zero.")
    minimum, maximum, count = elevation_range
    edges = np.array(
        [minimum + index * window_width for index in range(count + 1)],
        dtype=np.float64,
    )
    band_ids = np.digitize(dem_array, edges[1:-1], right=False).astype(np.int16) + 1
    band_ids = np.clip(band_ids, 1, count)

    folder = Path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    cell_area_m2 = pyflwdir_handler.cell_area_m2(dem)
    rows: list[dict[str, object]] = []
    outputs: list[Path] = []
    for index, watershed_path in enumerate(watershed_paths, start=1):
        watershed = _read_raster(watershed_path)
        watershed_array = watershed["array"]
        if watershed_array.shape != dem_array.shape:
            if log is not None:
                log(f"WARNING: Skipping watershed raster with mismatched shape: {watershed_path}")
            continue

        watershed_valid = valid_raster_mask(watershed_array, watershed["nodata"])
        valid = watershed_valid & (watershed_array > 0) & dem_valid
        name = _subcatchment_name(watershed_path, f"subcatchment_{index}")
        if not np.any(valid):
            if log is not None:
                log(f"WARNING: No valid DEM cells inside subcatchment: {name}")
            continue

        output_array = np.zeros(dem_array.shape, dtype=np.int16)
        output_array[valid] = band_ids[valid]
        output = _write_raster(
            folder / f"elevation_bands_{name}.tif",
            output_array,
            dem,
            nodata=0,
            data_type=gdal.GDT_Int16,
        )
        outputs.append(output)

        for band_id in range(1, count + 1):
            area_cells = int(np.count_nonzero(valid & (output_array == band_id)))
            area_m2 = area_cells * cell_area_m2
            rows.append(
                {
                    "subcatchment": name,
                    "band_id": band_id,
                    "min_elevation": edges[band_id - 1],
                    "max_elevation": edges[band_id],
                    "area_cells": area_cells,
                    "area_m2": area_m2,
                    "area_km2": area_m2 / 1_000_000.0,
                }
            )

    if not outputs:
        raise ValueError("No elevation band rasters were created.")
    summary = folder / "elevation_band_areas.csv"
    _write_summary(summary, rows)
    return ElevationBandResult(tuple(outputs), summary, minimum, maximum, count)
