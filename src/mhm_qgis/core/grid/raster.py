"""Inspect a DEM's effective grid without reading or deriving its pixels."""
from .model import crs_equal, geotransform_bounds, raster_resolution_info


def dem_grid(source, source_crs, target_crs, unit="", factor=1):
    """The DEM's grid in `target_crs`; with `factor`, the upscaled L0 grid: blocks of factor x factor
    cells from the lower-left corner, the top and right padded to whole blocks (as the upscaling does)."""
    from osgeo import gdal

    dataset = gdal.Open(str(source))
    if dataset is None:
        raise ValueError(f"Cannot open DEM: {source}")
    if not crs_equal(source_crs, target_crs):
        dataset = gdal.AutoCreateWarpedVRT(dataset, source_crs, target_crs, gdal.GRA_NearestNeighbour)
        if dataset is None:
            raise ValueError("Cannot determine the reprojected DEM grid.")
    transform = dataset.GetGeoTransform()
    if transform[2] or transform[4]:
        raise ValueError("The DEM must have a north-up grid.")
    xmin, ymin, xmax, ymax = geotransform_bounds(transform, dataset.RasterXSize, dataset.RasterYSize)
    cols, rows = -(-dataset.RasterXSize // factor), -(-dataset.RasterYSize // factor)
    xmax = xmin + cols * factor * (xmax - xmin) / dataset.RasterXSize
    ymax = ymin + rows * factor * (ymax - ymin) / dataset.RasterYSize
    return raster_resolution_info((xmin, xmax, ymin, ymax), cols, rows, unit=unit, crs=target_crs)
