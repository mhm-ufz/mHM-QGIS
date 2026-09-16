"""Variable units and time-interval conversions without changing input cadence."""
from __future__ import annotations

# Output name: source aliases, output unit, selectable input units.
VARIABLES = {
    "pre": (("pre", "tp", "total_precipitation", "Total_precipitation_surface_1_Hour_Accumulation"), "mm", ("mm", "m", "kg m-2", "kg m-2 s-1", "mm/day")),
    "pet": (("pet", "pev", "potential_evaporation"), "mm", ("mm", "m", "kg m-2", "kg m-2 s-1", "mm/day")),
    "tavg": (("tavg", "t2m", "2m_temperature", "Temperature_height_above_ground"), "Celsius", ("Celsius", "K")),
    "tmin": (("tmin", "minimum_temperature"), "Celsius", ("Celsius", "K")),
    "tmax": (("tmax", "maximum_temperature"), "Celsius", ("Celsius", "K")),
    "tann": (("tann", "annual_mean_temperature"), "Celsius", ("Celsius", "K")),
    "ssrd": (("ssrd", "surface_solar_radiation_downwards"), "W m-2", ("W m-2", "J m-2")),
    "strd": (("strd", "surface_thermal_radiation_downwards"), "W m-2", ("W m-2", "J m-2")),
    "eabs": (("eabs", "vapour_pressure", "vapor_pressure"), "Pa", ("Pa", "hPa", "kPa")),
    "windspeed": (("windspeed", "wind_speed"), "m s-1", ("m s-1", "km/h")),
}
CADENCES = {"Hourly": "h", "Daily": "D", "Monthly": "MS", "Yearly": "YS"}
ERA_END_VARIABLES = {"tp", "total_precipitation", "pev", "potential_evaporation",
                     "ssrd", "strd", "surface_solar_radiation_downwards",
                     "surface_thermal_radiation_downwards"}


def normalize_unit(unit):
    """Normalize common CF spellings, never silently change dimensions."""
    compact = str(unit).strip().lower().replace("**", "").replace("^", "").replace(" ", "")
    aliases = {"c": "Celsius", "degc": "Celsius", "degree_celsius": "Celsius",
               "degrees_celsius": "Celsius", "celsius": "Celsius", "kelvin": "K", "k": "K",
               "w/m2": "W m-2", "wm-2": "W m-2", "j/m2": "J m-2", "jm-2": "J m-2",
               "m/s": "m s-1", "ms-1": "m s-1", "kg/m2": "kg m-2",
               "kgm-2": "kg m-2", "kgm-2s-1": "kg m-2 s-1", "mmday-1": "mm/day",
               "pa": "Pa", "hpa": "hPa", "kpa": "kPa"}
    return aliases.get(compact, compact)


def validate_spec(spec):
    """Reject unsupported units, cadence and cumulative combinations."""
    if spec.temporal_resolution not in CADENCES:
        raise ValueError(f"{spec.kind}: select the input temporal resolution.")
    unit = normalize_unit(spec.unit)
    if unit not in VARIABLES[spec.kind][2]:
        raise ValueError(f"{spec.kind}: select a supported unit: {', '.join(VARIABLES[spec.kind][2])}.")
    if spec.cumulative:
        if spec.cumulative_resolution not in CADENCES:
            raise ValueError("Select the accumulation reset frequency.")
        if list(CADENCES).index(spec.cumulative_resolution) < list(CADENCES).index(spec.temporal_resolution):
            raise ValueError("Accumulation cannot reset more often than the input sampling.")
        if unit not in ("m", "mm", "kg m-2", "J m-2"):
            raise ValueError("Cumulative data must be an amount, not a rate or temperature.")


def time_index(values, cadence):
    """Validate an ordered continuous Gregorian coordinate against its declared cadence."""
    import numpy as np
    import pandas as pd
    values = np.asarray(values)
    if not np.issubdtype(values.dtype, np.datetime64):
        raise ValueError("Meteorology requires a Gregorian datetime coordinate.")
    index = pd.DatetimeIndex(values)
    if not len(index) or index.hasnans or index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("Meteorology timestamps must be nonempty, unique and ordered.")
    offset = {"Hourly": pd.DateOffset(hours=1), "Daily": pd.DateOffset(days=1),
              "Monthly": pd.DateOffset(months=1), "Yearly": pd.DateOffset(years=1)}[cadence]
    if cadence == "Monthly" and index.is_month_end.all():
        offset = pd.offsets.MonthEnd(1)
    if len(index) > 1 and not np.array_equal((index[:-1] + offset).values, index[1:].values):
        raise ValueError(f"Timestamps are not continuous {cadence.lower()} input.")
    return index, offset


def interval_bounds(ds, time_name, spec, source_name):
    """Read CF intervals or the known ERA interval-end convention."""
    import numpy as np
    index, offset = time_index(ds[time_name].values, spec.temporal_resolution)
    bounds_name = ds[time_name].attrs.get("bounds")
    if bounds_name and bounds_name in ds:
        bounds = np.asarray(ds[bounds_name].values).astype("datetime64[ns]")
    elif spec.cumulative and source_name in ERA_END_VARIABLES:
        bounds = np.column_stack(((index - offset).values, index.values))
    elif spec.cumulative:
        raise ValueError("Cumulative input requires CF time bounds or a recognized ERA interval-end variable.")
    else:
        bounds = np.column_stack((index.values, (index + offset).values))
    if (bounds.shape != (len(index), 2) or np.isnat(bounds).any()
            or (bounds[:, 1] <= bounds[:, 0]).any()
            or (bounds[:-1, 1] != bounds[1:, 0]).any()):
        raise ValueError("Meteorology time bounds must describe positive, contiguous intervals.")
    import pandas as pd
    _, bound_offset = time_index(bounds[:, 0], spec.temporal_resolution)
    if not np.array_equal((pd.DatetimeIndex(bounds[:, 0]) + bound_offset).values, bounds[:, 1]):
        raise ValueError("Time bounds differ from the selected input cadence.")
    return bounds


def convert_values(values, bounds, spec, source_name, previous=None):
    """Convert a time block to model units, retaining the last raw cumulative plane."""
    import numpy as np
    import pandas as pd
    unit = normalize_unit(spec.unit)
    result = np.asarray(values, dtype="float64").copy()
    if spec.cumulative:
        frequency = {"Hourly": "h", "Daily": "D", "Monthly": "M", "Yearly": "Y"}[spec.cumulative_resolution]
        starts = pd.DatetimeIndex(bounds[:, 0])
        groups = starts.to_period(frequency)
        for number, group in enumerate(groups):
            raw = result[number].copy()
            at_reset = starts[number] == group.start_time
            if not at_reset:
                if previous is None or previous[0] != group or previous[2] != bounds[number, 0]:
                    raise ValueError("Cumulative data starts mid-cycle without its preceding sample.")
                result[number] -= previous[1]
            previous = (group, raw, bounds[number, 1])
    seconds = (bounds[:, 1] - bounds[:, 0]) / np.timedelta64(1, "s")
    seconds = seconds.reshape((-1,) + (1,) * (result.ndim - 1))
    if unit == "K":
        result -= 273.15
    elif unit == "m":
        result *= 1000.0
    elif unit == "kg m-2 s-1":
        result *= seconds
    elif unit == "mm/day":
        result *= seconds / 86400.0
    elif unit == "J m-2":
        result /= seconds
    elif unit == "hPa":
        result *= 100.0
    elif unit == "kPa":
        result *= 1000.0
    elif unit == "km/h":
        result /= 3.6
    if spec.kind == "pet" and source_name in {"pev", "potential_evaporation"}:
        result *= -1
    if spec.kind in {"pre", "pet"} and np.any(result < -1e-7):
        raise ValueError(f"{spec.kind}: negative interval amounts; check cumulative settings or source sign.")
    if spec.kind in {"pre", "pet"}:
        np.maximum(result, 0, out=result)
    return result, previous
