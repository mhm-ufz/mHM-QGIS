"""mHM forcing variable definitions for ERA5-Land inputs."""
from __future__ import annotations

from .types import ForcingSpec

TEMPERATURE_FILE_VARIABLE = "2m_temperature"
PRECIPITATION_FILE_VARIABLE = "total_precipitation"

VARIABLE_NAME_MAPPINGS = {
    "precipitation": (
        "tp",
        "Total_precipitation_surface_1_Hour_Accumulation",
        "total_precipitation",
    ),
    "temperature": (
        "t2m",
        "Temperature_height_above_ground",
        "2m_temperature",
    ),
    "potential_evaporation": ("pev", "potential_evaporation"),
}

FORCING_SPECS = (
    ForcingSpec(
        output_variable="pre",
        output_folder="pre",
        source_key="precipitation",
        file_variable=PRECIPITATION_FILE_VARIABLE,
        # ERA5-Land total precipitation is accumulated in metres. The 00 UTC
        # value represents the previous day's 24-hour total.
        aggregation="era5land_daily_total",
        units="mm",
        long_name="daily total precipitation",
        scale=1000.0,
    ),
    ForcingSpec(
        output_variable="tavg",
        output_folder="tavg",
        source_key="temperature",
        file_variable=TEMPERATURE_FILE_VARIABLE,
        aggregation="mean",
        units="Celsius",
        long_name="daily mean 2 metre temperature",
        offset=-273.15,
    ),
    ForcingSpec(
        output_variable="tmin",
        output_folder="tmin",
        source_key="temperature",
        file_variable=TEMPERATURE_FILE_VARIABLE,
        aggregation="min",
        units="Celsius",
        long_name="daily minimum 2 metre temperature",
        offset=-273.15,
    ),
    ForcingSpec(
        output_variable="tmax",
        output_folder="tmax",
        source_key="temperature",
        file_variable=TEMPERATURE_FILE_VARIABLE,
        aggregation="max",
        units="Celsius",
        long_name="daily maximum 2 metre temperature",
        offset=-273.15,
    ),
)
