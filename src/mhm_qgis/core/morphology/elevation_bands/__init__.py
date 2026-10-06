"""QGIS-free elevation-band raster and land-cover reporting API."""

from .bands import (
    ElevationBandResult,
    clean_output_name,
    collect_elevation_band_rasters,
    collect_watershed_rasters,
    create_elevation_bands,
    elevation_range_from_window_width,
    nice_step,
    raster_value_range,
    valid_raster_mask,
)
from .land_cover import (
    BandLandCoverResult,
    create_band_land_cover_details,
    read_land_cover_class_names,
)

__all__ = [
    "BandLandCoverResult",
    "ElevationBandResult",
    "clean_output_name",
    "collect_elevation_band_rasters",
    "collect_watershed_rasters",
    "create_band_land_cover_details",
    "create_elevation_bands",
    "elevation_range_from_window_width",
    "nice_step",
    "raster_value_range",
    "read_land_cover_class_names",
    "valid_raster_mask",
]
