"""Inspect a reference raster's effective grid without reading or deriving its pixels."""
from .model import crs_equal, geotransform_bounds, raster_resolution_info


def raster_grid(source, source_crs, target_crs, unit="", factor=1):
    """The raster's grid in `target_crs` (a GDAL source: a path, or `NETCDF:"path":var`); with
    `factor`, the upscaled grid: blocks of factor x factor cells from the lower-left corner, the
    top and right padded to whole blocks (as the topography upscaling does)."""
    from osgeo import gdal

    text = str(source)
    if text.startswith('NETCDF:"'):
        dataset = _netcdf_grid(text, source_crs)
    else:
        dataset = gdal.Open(text)
        if dataset is None:
            raise ValueError(f"Cannot open the raster: {source}")
        if dataset.GetGeoTransform(can_return_null=True) is None:
            raise ValueError(f"{source} has no grid information (lower-left corner and cell size): "
                             "select another file.")
    if not crs_equal(source_crs, target_crs):
        dataset = gdal.AutoCreateWarpedVRT(dataset, source_crs, target_crs, gdal.GRA_NearestNeighbour)
        if dataset is None:
            raise ValueError("Cannot determine the reprojected raster grid.")
    transform = dataset.GetGeoTransform()
    if transform[2] or transform[4]:
        raise ValueError("The raster must have a north-up grid.")
    cols, rows = dataset.RasterXSize, dataset.RasterYSize
    xmin, ymin, xmax, ymax = geotransform_bounds(transform, cols, rows)
    blocks_x, blocks_y = -(-cols // factor), -(-rows // factor)
    xmax = xmin + blocks_x * factor * (xmax - xmin) / cols
    ymax = ymin + blocks_y * factor * (ymax - ymin) / rows
    return raster_resolution_info((xmin, xmax, ymin, ymax), blocks_x, blocks_y, unit=unit, crs=target_crs)


def _netcdf_grid(source, crs):
    """A NetCDF variable's grid read as the forcing reads it (from its axes, never its bands), as a
    data-less VRT that GDAL can warp."""
    from osgeo import gdal, osr

    from ..meteorology.forcing.api import netcdf_grid

    (xmin, xmax, ymin, ymax), cols, rows, _declared = netcdf_grid(source.split('"')[1], source.rsplit(":", 1)[1], crs)
    dataset = gdal.GetDriverByName("VRT").Create("", cols, rows, 1)
    dataset.SetGeoTransform((xmin, (xmax - xmin) / cols, 0, ymax, 0, -(ymax - ymin) / rows))
    if crs:
        reference = osr.SpatialReference()
        reference.SetFromUserInput(crs)
        dataset.SetProjection(reference.ExportToWkt())
    return dataset
