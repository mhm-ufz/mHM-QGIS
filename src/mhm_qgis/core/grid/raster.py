"""Inspect a DEM's effective grid without reading or deriving its pixels."""
from .model import crs_equal, geotransform_bounds, raster_resolution_info


def dem_grid(source, source_crs, target_crs, unit=""):
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
    return raster_resolution_info(
        (xmin, xmax, ymin, ymax),
        dataset.RasterXSize, dataset.RasterYSize, unit=unit, crs=target_crs)
