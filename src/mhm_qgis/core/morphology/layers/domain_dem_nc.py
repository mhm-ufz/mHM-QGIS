"""Write one domain DEM on the common L0 grid for mHM v6."""
from ...handlers.file.netcdf.raster import copy_rasters
from ...handlers.raster.tasks import _dependencies, _target_mask


def write_domain_dem_netcdf(source_path, output_path, target_header, mask_path, *, reference_path=None):
    """Replace the domain DEM atomically, never writing through a shared link."""
    _np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    dataset = gdal.Open(str(source_path))
    if dataset is None:
        raise RuntimeError(f"Cannot open domain DEM source: {source_path}")
    projection = dataset.GetProjection()
    dataset = None
    keep = _target_mask(mask_path, target_header, projection, reference_path)
    return copy_rasters(output_path, {"dem": source_path}, target_header,
                        reference_path=reference_path, mask=keep)


__all__ = ["write_domain_dem_netcdf"]
