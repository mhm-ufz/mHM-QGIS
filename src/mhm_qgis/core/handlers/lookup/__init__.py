"""CSV and TXT lookup tables, and the manifests built from them.

Four concerns, one vocabulary:

* `table`     -- read a lookup table and resolve its columns. Every name
                 comparison goes through `normalize_key`, because column names
                 carry `[unit]` suffixes and a `*` prefix for columns
                 mHM-tools ignores.
* `manifests` -- the validated `format-data.csv` files for time-varying land
                 cover and layered soil, written atomically.
* `geology`   -- geology parameter metadata derived from a lookup table.
* `job`       -- the QGIS-free runner that prepares one categorical input and
                 publishes its outputs atomically.

Nothing here imports QGIS: `job` runs in a child process precisely because
formatting a vector categorical input on an L0 grid peaked at 5.5 GiB and was
OOM-killing the QGIS process.
"""
from .geology import write_geology_metadata
from .job import run_lookup_job
from .manifests import (
    BULK_DENSITY_UNITS,
    MAX_LAND_USE_PERIODS,
    MAX_SOIL_HORIZONS,
    LandUseInput,
    LandUsePeriod,
    SoilHorizon,
    SoilInput,
    validate_land_use_input,
    validate_soil_input,
    write_land_use_manifest,
    write_soil_manifest,
)
from .table import (
    as_csv,
    category_key,
    columns,
    normalize_key,
    optional_field,
    read,
    resolve_field,
    resolve_vector_mapping_field,
    visible_columns,
)

__all__ = [
    "BULK_DENSITY_UNITS",
    "MAX_LAND_USE_PERIODS",
    "MAX_SOIL_HORIZONS",
    "LandUseInput",
    "LandUsePeriod",
    "SoilHorizon",
    "SoilInput",
    "as_csv",
    "category_key",
    "columns",
    "normalize_key",
    "optional_field",
    "read",
    "resolve_field",
    "resolve_vector_mapping_field",
    "run_lookup_job",
    "validate_land_use_input",
    "validate_soil_input",
    "visible_columns",
    "write_geology_metadata",
    "write_land_use_manifest",
    "write_soil_manifest",
]
