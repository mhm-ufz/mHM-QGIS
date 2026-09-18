# -*- coding: utf-8 -*-
"""Assemble the masked L0 morphology rasters into one v6 `input.nc`.

mHM v6 reads its static morphology from a single NetCDF whose variables share
the model grid, instead of the separate Arc/Info ASCII files v5.13 expects. The
layout follows `examples/domain_01/input/morph/input.nc`: projected `x`/`y` axes
with bounds, 2-D `lon`/`lat`, and one variable per morphology layer.

Rows are written in blocks so a continental grid costs flat memory, the same way
the LAI and domain-DEM writers work.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from ...grid import header_center_arrays
from ...handlers.raster.tasks import _aligned_l0_window, _dependencies


NODATA = -9999
BLOCK_ROWS = 256

# Variable name -> (masked raster stem, dtype, attributes).
MORPH_VARIABLES = (
    ("dem", "dem", "f8", {
        "long_name": "elevation",
        "standard_name": "height_above_mean_sea_level",
        "units": "m",
    }),
    ("fdir", "fdir", "i4", {
        "long_name": "d8 flow direction",
    }),
    ("slope", "slope", "f8", {
        "long_name": "slope",
        "standard_name": "ground_slope_angle",
        "units": "%",
    }),
    ("aspect", "aspect", "f8", {
        "long_name": "aspect",
        "units": "degree",
    }),
    ("geology_class", "3_geology_processed", "i4", {
        "long_name": "geology class",
        "units": "1",
    }),
    ("soil_class", "3_soil", "i4", {
        "long_name": "soil class",
        "units": "1",
    }),
    ("LAI_class", "LAI_class", "i4", {
        "long_name": "leaf area index class",
        "units": "1",
    }),
    ("facc", "facc", "i4", {
        "long_name": "flow accumulation",
        "units": "1",
    }),
)


def write_morph_input_nc(
        output_path,
        layers: Mapping[str, Path | str],
        target_header: Mapping[str, Any],
        *,
        crs_string=None,
        reference_path=None,
        title="Static morphology inputs",
        log=None) -> Path:
    """Write `input.nc` from the masked rasters already on the L0 grid."""
    import numpy as np
    from netCDF4 import Dataset

    if not layers:
        raise ValueError("No masked morphology rasters were found for input.nc.")
    _np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()

    rows = int(target_header["nrows"])
    columns = int(target_header["ncols"])
    cellsize = float(target_header["cellsize"])
    x_values, y_values = header_center_arrays(target_header)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.unlink(missing_ok=True)
    chunks = (min(rows, 128), min(columns, 1024))
    try:
        with Dataset(temporary, "w", format="NETCDF4") as handle:
            handle.createDimension("x", columns)
            handle.createDimension("y", rows)
            handle.createDimension("bnds", 2)
            _write_axes(handle, x_values, y_values, cellsize)
            _write_lonlat(handle, x_values, y_values, crs_string, chunks)

            for name, _stem, dtype, attrs in MORPH_VARIABLES:
                source_path = layers.get(name)
                if source_path is None:
                    continue
                variable = handle.createVariable(
                    name, dtype, ("y", "x"),
                    zlib=True, complevel=1, chunksizes=chunks,
                    fill_value=NODATA,
                )
                variable.setncatts({
                    **attrs,
                    "coordinates": "lat lon",
                    "missing_value": NODATA,
                })
                _copy_layer(
                    variable, source_path, target_header, reference_path,
                    rows, columns, gdal, np,
                )
                if log:
                    log(f"input.nc: wrote {name} from {Path(source_path).name}.")

            handle.Conventions = "CF-1.8"
            handle.title = str(title)
            handle.history = "Prepared for mHM by mhm_qgis"
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    if output.exists():
        output.unlink()
    os.replace(temporary, output)
    return output


def _write_axes(handle, x_values, y_values, cellsize) -> None:
    """Write the projected axes and their cell bounds."""
    import numpy as np

    x = handle.createVariable("x", "f8", ("x",))
    x.setncatts({
        "long_name": "x coordinate of projection",
        "standard_name": "projection_x_coordinate",
        "units": "m",
        "axis": "X",
        "bounds": "x_bnds",
    })
    x[:] = x_values
    y = handle.createVariable("y", "f8", ("y",))
    y.setncatts({
        "long_name": "y coordinate of projection",
        "standard_name": "projection_y_coordinate",
        "units": "m",
        "axis": "Y",
        "bounds": "y_bnds",
    })
    y[:] = y_values
    half = cellsize / 2.0
    handle.createVariable("x_bnds", "f8", ("x", "bnds"))[:, :] = np.column_stack(
        (x_values - half, x_values + half))
    handle.createVariable("y_bnds", "f8", ("y", "bnds"))[:, :] = np.column_stack(
        (y_values + half, y_values - half))


def _write_lonlat(handle, x_values, y_values, crs_string, chunks) -> None:
    """Write 2-D lon/lat, replacing the separate latlon.nc v5.13 needs."""
    import numpy as np

    longitude = handle.createVariable(
        "lon", "f8", ("y", "x"), zlib=True, complevel=4, chunksizes=chunks)
    longitude.setncatts({
        "long_name": "longitude",
        "standard_name": "longitude",
        "units": "degrees_east",
    })
    latitude = handle.createVariable(
        "lat", "f8", ("y", "x"), zlib=True, complevel=4, chunksizes=chunks)
    latitude.setncatts({
        "long_name": "latitude",
        "standard_name": "latitude",
        "units": "degrees_north",
    })
    for start in range(0, y_values.size, BLOCK_ROWS):
        stop = min(start + BLOCK_ROWS, y_values.size)
        block_x, block_y = np.meshgrid(x_values, y_values[start:stop])
        longitude[start:stop, :], latitude[start:stop, :] = _to_wgs84(
            block_x, block_y, crs_string)


def _to_wgs84(block_x, block_y, crs_string):
    """Return WGS84 lon/lat for one block, identity for a geographic CRS."""
    from pyproj import CRS, Transformer

    if not crs_string or CRS.from_user_input(crs_string).is_geographic:
        return block_x, block_y

    transform = Transformer.from_crs(crs_string, "EPSG:4326", always_xy=True)
    return transform.transform(block_x, block_y)


def _copy_layer(
        variable, source_path, target_header, reference_path,
        rows, columns, gdal, np) -> None:
    """Copy one masked raster into its variable, block by block."""
    source = gdal.Open(str(source_path), gdal.GA_ReadOnly)
    if source is None:
        raise RuntimeError(f"Could not open the masked raster: {source_path}")
    try:
        window = _aligned_l0_window(
            source, source_path, target_header, reference_path)
        band = source.GetRasterBand(1)
        source_nodata = band.GetNoDataValue()
        blank = np.full((1, columns), float(NODATA), dtype="float64")
        for start in range(0, rows, BLOCK_ROWS):
            stop = min(start + BLOCK_ROWS, rows)
            block = np.repeat(blank, stop - start, axis=0)
            overlap_start = max(start, window["target_y"])
            overlap_stop = min(stop, window["target_y"] + window["copy_rows"])
            if window["copy_rows"] > 0 and overlap_stop > overlap_start:
                read = band.ReadAsArray(
                    window["source_x"],
                    window["source_y"] + (overlap_start - window["target_y"]),
                    window["copy_cols"],
                    overlap_stop - overlap_start,
                )
                if read is None:
                    raise RuntimeError(
                        f"Could not read the masked raster: {source_path}")
                block[
                    overlap_start - start: overlap_stop - start,
                    window["target_x"]: window["target_x"] + window["copy_cols"],
                ] = read
            if source_nodata is not None:
                block = np.where(block == source_nodata, float(NODATA), block)
            variable[start:stop, :] = block
    finally:
        source = None


__all__ = [
    "MORPH_VARIABLES",
    "write_morph_input_nc",
]
