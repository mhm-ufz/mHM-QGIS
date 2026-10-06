"""QGIS layer handling: build a layer, register it, find it, ask its CRS.

The one place in the plugin that knows the `QgsRasterLayer` / `QgsVectorLayer` /
`QgsProject` vocabulary. Everything above it -- the Qt dialogs, the display
helpers, the Morphology processors -- goes through this API instead of touching
the QGIS layer classes directly, so the version shims and the validity checks
live once.

QGIS is imported inside the functions, never at module scope, so `qgis_bridge`
stays importable before a QGIS application is initialized.
"""
from .compat import create_vector_file_writer, qgs_field
from .crs import crs_of, transform_between, transform_to_raster
from .gauges import gauge_coordinates
from .grid import (
    crs_string,
    crs_unit,
    extent_bounds,
    merged_domain_bounds,
    raster_resolution_info,
)
from .loader import load, open_layer, raster_source, refresh_source, source_uri
from .materialize import (
    local_layer_source,
    materialize_raster_layer,
    materialize_vector_layer,
    remove_vector_dataset,
)
from .project import (
    add,
    find_by_name,
    find_by_source,
    normalized_source,
    remove_under,
)

__all__ = [
    "add",
    "create_vector_file_writer",
    "crs_of",
    "crs_string",
    "crs_unit",
    "extent_bounds",
    "find_by_name",
    "find_by_source",
    "gauge_coordinates",
    "load",
    "local_layer_source",
    "materialize_raster_layer",
    "materialize_vector_layer",
    "merged_domain_bounds",
    "normalized_source",
    "open_layer",
    "qgs_field",
    "raster_resolution_info",
    "raster_source",
    "refresh_source",
    "remove_under",
    "remove_vector_dataset",
    "source_uri",
    "transform_between",
    "transform_to_raster",
]
