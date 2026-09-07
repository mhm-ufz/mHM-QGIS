"""Exact, bounded-memory NetCDF raster copies, with GDAL-readable grid metadata."""
from contextlib import contextmanager
import os
from pathlib import Path

from ....grid import (
    aligned_window, crs_equal, geotransform_from_header,
    header_center_arrays, header_from_geotransform,
)


def source(path, variable=None):
    """Address a NetCDF variable without extracting another raster."""
    return f'NETCDF:"{path}":{variable}' if variable else str(path)


def file_path(value):
    """Return the physical path behind a GDAL NetCDF source."""
    value = str(value)
    return Path(value.split('"', 2)[1] if value.startswith('NETCDF:"') else value)


def variables(path):
    """Read variable names only; missing files have no variables."""
    from netCDF4 import Dataset

    if not Path(path).is_file():
        return ()
    with Dataset(path) as handle:
        return tuple(handle.variables)


@contextmanager
def _writer(path, header, projection):
    from netCDF4 import Dataset
    from pyproj import CRS

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with Dataset(temporary, "w", format="NETCDF4") as handle:
            x, y = header_center_arrays(header)
            crs = CRS.from_user_input(projection)
            for name, values in (("x", x), ("y", y)):
                handle.createDimension(name, len(values))
                axis = handle.createVariable(name, "f8", (name,))
                standard = ("longitude" if name == "x" else "latitude") if crs.is_geographic else f"projection_{name}_coordinate"
                units = ("degrees_east" if name == "x" else "degrees_north") if crs.is_geographic else crs.axis_info[0].unit_name
                axis.setncatts({"axis": name.upper(), "standard_name": standard, "units": units})
                axis[:] = values
            mapping = handle.createVariable("crs", "i4")
            mapping.assignValue(0)
            mapping.setncatts({**crs.to_cf(), "spatial_ref": crs.to_wkt(),
                              "GeoTransform": " ".join(map(str, geotransform_from_header(header)))})
            handle.Conventions = "CF-1.8"
            yield handle
        # Replace the directory entry, not the target of an existing symlink.
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _variable(handle, name, dtype, nodata, attrs=None):
    variable = handle.createVariable(
        name, dtype, ("y", "x"), fill_value=nodata,
        zlib=True, complevel=1,
        chunksizes=(min(256, len(handle.dimensions["y"])), min(1024, len(handle.dimensions["x"]))),
    )
    variable.setncatts({**(attrs or {}), "grid_mapping": "crs"})
    return variable


def write_array(path, name, array, reference, nodata):
    """Write an already computed raster, preserving its grid and dtype."""
    header = header_from_geotransform(reference["geotransform"], reference["cols"], reference["rows"])
    with _writer(path, header, reference["projection"]) as handle:
        _variable(handle, name, array.dtype, nodata)[:] = array
    return str(path)


def copy_rasters(path, sources, header=None, *, reference_path=None, mask=None, task=None):
    """Copy named GDAL sources onto one exact grid; never interpolate L0."""
    import numpy as np
    from osgeo import gdal, gdal_array

    reference = gdal.Open(str(reference_path or next(iter(sources.values()))))
    if reference is None:
        raise ValueError("Cannot open the NetCDF reference grid.")
    reference_header = header_from_geotransform(reference.GetGeoTransform(), reference.RasterXSize, reference.RasterYSize)
    header = header or reference_header
    projection = reference.GetProjection()
    reference = None
    if not projection:
        raise ValueError("The NetCDF reference grid has no CRS.")
    aligned_window(reference_header, header)
    with _writer(path, header, projection) as handle:
        for name, uri in sources.items():
            dataset = gdal.Open(str(uri))
            if dataset is None or dataset.RasterCount != 1:
                raise ValueError(f"Expected one raster variable: {uri}")
            try:
                if not crs_equal(dataset.GetProjection(), projection):
                    raise ValueError(f"Raster CRS differs from the reference: {uri}")
                window = aligned_window(header_from_geotransform(
                    dataset.GetGeoTransform(), dataset.RasterXSize, dataset.RasterYSize), header)
                band = dataset.GetRasterBand(1)
                nodata = band.GetNoDataValue()
                nodata = -9999 if nodata is None else nodata
                dtype = np.dtype(gdal_array.GDALTypeCodeToNumericTypeCode(band.DataType))
                attrs = {key: band.GetMetadataItem(key) for key in ("units", "long_name") if band.GetMetadataItem(key)}
                variable = _variable(handle, name, dtype, nodata, attrs)
                if window["copy_cols"] <= 0 or window["copy_rows"] <= 0:
                    continue
                for row in range(0, window["copy_rows"], 256):
                    if task is not None and task.isCanceled():
                        raise RuntimeError("Task cancelled.")
                    height = min(256, window["copy_rows"] - row)
                    values = band.ReadAsArray(window["source_x"], window["source_y"] + row, window["copy_cols"], height)
                    if values is None:
                        raise RuntimeError(f"Cannot read raster window: {uri}")
                    y, x = window["target_y"] + row, window["target_x"]
                    if mask is not None:
                        values = np.where(mask[y:y + height, x:x + window["copy_cols"]], values, nodata)
                    variable[y:y + height, x:x + window["copy_cols"]] = values
            finally:
                band = None
                dataset = None
    return Path(path)
