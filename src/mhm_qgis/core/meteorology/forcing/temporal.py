"""Variable units and bounded-memory temporal aggregation."""
from __future__ import annotations

from contextlib import contextmanager

# Output name: source aliases, output unit, selectable input units.
VARIABLES = {
    "pre": (("pre", "tp", "total_precipitation", "Total_precipitation_surface_1_Hour_Accumulation"), "mm", ("mm", "m", "kg m-2", "kg m-2 s-1", "mm/day")),
    "pet": (("pet", "pev", "potential_evaporation"), "mm", ("mm", "m", "kg m-2", "kg m-2 s-1", "mm/day")),
    "tavg": (("tavg", "t2m", "2m_temperature", "Temperature_height_above_ground"), "Celsius", ("Celsius", "K")),
    "tmin": (("tmin", "minimum_temperature", "t2m", "2m_temperature", "Temperature_height_above_ground"), "Celsius", ("Celsius", "K")),
    "tmax": (("tmax", "maximum_temperature", "t2m", "2m_temperature", "Temperature_height_above_ground"), "Celsius", ("Celsius", "K")),
    "tann": (("tann", "annual_mean_temperature"), "Celsius", ("Celsius", "K")),
    "ssrd": (("ssrd", "surface_solar_radiation_downwards"), "W m-2", ("W m-2", "J m-2")),
    "strd": (("strd", "surface_thermal_radiation_downwards"), "W m-2", ("W m-2", "J m-2")),
    "netrad": (("netrad", "net_radiation", "surface_net_radiation"), "W m-2", ("W m-2", "J m-2")),
    "eabs": (("eabs", "vapour_pressure", "vapor_pressure"), "Pa", ("Pa", "hPa", "kPa")),
    "windspeed": (("windspeed", "wind_speed"), "m s-1", ("m s-1", "km/h")),
    # Additional observations are already expressed in their model units.
    "et": (("et", "evapotranspiration"), "mm", ("mm",)),
    "sm": (("sm", "soil_moisture"), "m3 m-3", ("m3 m-3",)),
    "neutrons": (("neutrons",), "counts", ("counts",)),
    "twsa": (("twsa", "tws"), "mm", ("mm",)),
}
CADENCES = {"Hourly": "h", "Daily": "D", "Monthly": "MS", "Yearly": "YS"}
# `datetime64` units are calendar units: `[M] + 1` is the next month and
# `[Y] + 1` the next year, so numpy does the 28/29/30/31-day and leap handling
# that pandas offsets used to -- in C, over a whole chunk at once.
VARIABLE_LABELS = {
    "pre": "Precipitation", "pet": "Potential evapotranspiration", "tavg": "Mean temperature",
    "tmin": "Minimum temperature", "tmax": "Maximum temperature", "tann": "Annual mean temperature",
    "ssrd": "Shortwave radiation", "strd": "Longwave radiation", "netrad": "Net radiation",
    "eabs": "Vapour pressure", "windspeed": "Wind speed",
}
PERIOD_UNITS = {"Hourly": "h", "Daily": "D", "Monthly": "M", "Yearly": "Y"}
# The last hour, day or month an accumulation covers before it resets; the last one by default.
RESETS = {"Daily": range(24), "Monthly": range(1, 32), "Yearly": range(1, 13)}
AGGREGATIONS = ("Average", "Sum", "Minimum", "Maximum")
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
               "mmd-1": "mm/day", "mm/d": "mm/day",
               "pa": "Pa", "hpa": "hPa", "kpa": "kPa",
               "m3s-1": "m3 s-1", "m3m-3": "m3 m-3", "counts": "counts"}
    return aliases.get(compact, compact)


def validate_spec(spec):
    """Reject unsupported units, cadence and cumulative combinations."""
    if spec.temporal_resolution not in CADENCES:
        raise ValueError("Select the input temporal resolution.")
    output = spec.output_temporal_resolution or spec.temporal_resolution
    if output not in CADENCES:
        raise ValueError("Select the output temporal resolution.")
    input_index = list(CADENCES).index(spec.temporal_resolution)
    output_index = list(CADENCES).index(output)
    if output_index < input_index:
        raise ValueError("Output temporal resolution cannot be finer than input.")
    if output_index == input_index and spec.aggregation:
        raise ValueError("Aggregation must be <None> when input and output resolutions match.")
    if output_index > input_index and spec.aggregation not in AGGREGATIONS:
        raise ValueError("Select an aggregation type for the coarser output resolution.")
    unit = normalize_unit(spec.unit)
    if unit not in VARIABLES[spec.kind][2]:
        raise ValueError(f"Select a supported unit: {', '.join(VARIABLES[spec.kind][2])}.")
    if spec.cumulative:
        if spec.cumulative_resolution not in CADENCES:
            raise ValueError("Select the accumulation reset frequency.")
        if list(CADENCES).index(spec.cumulative_resolution) < list(CADENCES).index(spec.temporal_resolution):
            raise ValueError("Accumulation cannot reset more often than the input sampling.")
        if unit not in ("m", "mm", "kg m-2", "J m-2"):
            raise ValueError("Cumulative data must be an amount, not a rate or temperature.")
        if spec.cumulative_reset is not None and spec.cumulative_reset not in RESETS.get(spec.cumulative_resolution, ()):
            raise ValueError("Select when the accumulation resets.")


def output_periods(starts, cadence):
    """Return the calendar interval key containing each input interval start.

    One truncation for the whole array. The per-timestep `pd.Timestamp` plus
    `pd.DateOffset` this replaced fell through to `dateutil.relativedelta` and
    cost about 38 us a step, which was the largest single item in a long run.
    """
    import numpy as np

    return np.asarray(starts).astype(f"datetime64[{PERIOD_UNITS[cadence]}]")


def period_bounds(key):
    """Return the nanosecond start and end of one calendar interval key."""
    return key.astype("datetime64[ns]"), (key + 1).astype("datetime64[ns]")


def covered_until(last_start, cadence) -> str:
    """The end of the last complete `cadence` period, which starts at `last_start`."""
    import numpy as np

    return str(period_bounds(output_periods(np.datetime64(last_start[:16]), cadence))[1])[:16]


def covers_end(sim_end, last_start, cadence, v6) -> bool:
    """v6 runs up to `sim_end` (exclusive); v5.13 through the whole day `sim_end`."""
    covered = covered_until(last_start, cadence)
    return sim_end[:16] <= covered if v6 else sim_end[:10] < covered[:10]


def time_index(values, cadence, *, continuous=True):
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
    # one stamp in each consecutive calendar period, wherever in it (mid-month, month end, 23:00)
    if continuous and len(index) > 1 and not (np.diff(output_periods(index.values, cadence).astype("int64")) == 1).all():
        raise ValueError(f"Timestamps are not continuous {cadence.lower()} input.")
    return index, offset


def interval_bounds(ds, time_name, spec, source_name):
    """Read CF intervals or the known ERA interval-end convention."""
    import numpy as np
    bounds_name = ds[time_name].attrs.get("bounds")
    index, offset = time_index(
        ds[time_name].values, spec.temporal_resolution,
        continuous=not (bounds_name and bounds_name in ds),
    )
    if bounds_name and bounds_name in ds:
        bounds = np.asarray(ds[bounds_name].values).astype("datetime64[ns]")
    elif source_name in ERA_END_VARIABLES and (
            spec.cumulative or (
                spec.aggregation in {"Minimum", "Maximum"}
                and spec.temporal_resolution == "Hourly"
                and spec.output_temporal_resolution == "Daily"
            )):
        bounds = np.column_stack(((index - offset).values, index.values))
    elif spec.cumulative:
        raise ValueError("Cumulative input requires CF time bounds or a recognized ERA interval-end variable.")
    else:  # the calendar period holding each stamp: month-end or 23:00 stamps are whole months and days
        bounds = np.column_stack(period_bounds(output_periods(index.values, spec.temporal_resolution)))
    if (bounds.shape != (len(index), 2) or np.isnat(bounds).any()
            or (bounds[:, 1] <= bounds[:, 0]).any()
            or (bounds[:-1, 1] != bounds[1:, 0]).any()):
        raise ValueError("Meteorology time bounds must describe positive, contiguous intervals.")
    return bounds


def complete_window(bounds, input_cadence, output_cadence):
    """Return the first through last complete output periods, excluding edge fragments."""
    import numpy as np

    def complete(starts, ends, cadence):
        keys = output_periods(starts, cadence)
        expected_start, expected_end = period_bounds(keys)
        return (starts == expected_start) & (ends == expected_end)

    input_full = complete(bounds[:, 0], bounds[:, 1], input_cadence)
    if not input_full[1:-1].all():
        raise ValueError("Meteorology contains an incomplete input interval inside the record.")
    keys = output_periods(bounds[:, 0], output_cadence)
    edges = np.r_[0, np.flatnonzero(keys[1:] != keys[:-1]) + 1, len(bounds)]
    starts, ends = bounds[edges[:-1], 0], bounds[edges[1:] - 1, 1]
    full = complete(starts, ends, output_cadence)
    selected = np.flatnonzero(full)
    if not len(selected) or not full[selected[0]:selected[-1] + 1].all():
        raise ValueError("Meteorology has no continuous complete output periods.")
    return starts[selected[0]], ends[selected[-1]], starts[selected[-1]]


def deaccumulate_values(values, bounds, spec, previous=None):
    """Return interval values while retaining the last cumulative plane."""
    import numpy as np
    import pandas as pd

    # Only the cumulative branch writes into the block, so a non-cumulative
    # variable must not pay a full copy of every chunk for nothing.
    result = np.asarray(values, dtype="float64")
    if spec.cumulative:
        result = result.copy()
        opens = cycle_starts(pd.DatetimeIndex(bounds[:, 0]), spec.cumulative_resolution, spec.cumulative_reset)
        for number, opened in enumerate(opens):
            raw = result[number].copy()
            if not opened and previous is None:  # the record's leading fragment: before its window
                result[number] = np.nan
            elif not opened:
                if previous[1] != bounds[number, 0]:
                    raise ValueError("Cumulative data starts mid-cycle without its preceding sample.")
                result[number] -= previous[0]
            previous = (raw, bounds[number, 1])
    return result, previous


def cycle_starts(starts, cadence, reset=None):
    """Whether each interval start opens an accumulation cycle: the `reset` hour, day or month (the
    last a cycle covers) has just ended. A day past the month's length is its last day."""
    import pandas as pd

    last = RESETS[cadence][-1] if reset is None else reset
    if cadence == "Daily":
        return (starts.hour == (last + 1) % 24) & (starts == starts.floor("h"))
    midnight = starts == starts.normalize()
    if cadence == "Monthly":
        return midnight & ((starts.day == last + 1) | ((starts.day == 1) & ((starts - pd.Timedelta(days=1)).days_in_month <= last)))
    return midnight & (starts.day == 1) & (starts.month == last % 12 + 1)


@contextmanager
def about_variable(kind):
    """Name the variable in a failure inside, so the user knows which input to fix."""
    try:
        yield
    except ValueError as error:
        raise ValueError(f"{VARIABLE_LABELS.get(kind, kind)}: {error}") from error


class NegativeAmounts(Exception):
    """Amounts slightly below zero, as de-accumulated ERA5 leaves them; clipped once the user agrees."""

    def __init__(self, kind, lowest):
        self.kind = kind
        super().__init__(f"{VARIABLE_LABELS.get(kind, kind)} has small negative amounts (down to {lowest:.3g} mm), "
                         "most likely rounding noise of the source data.")


def convert_units(values, bounds, spec, source_name, *, input_seconds=None):
    """Convert already aggregated values to the model unit."""
    import numpy as np

    unit = normalize_unit(spec.unit)
    result = np.asarray(values, dtype="float64").copy()
    seconds = (bounds[:, 1] - bounds[:, 0]) / np.timedelta64(1, "s")
    seconds = seconds.reshape((-1,) + (1,) * (result.ndim - 1))
    source_seconds = float(input_seconds or seconds.reshape(-1)[0])
    if unit == "K":
        result -= 273.15
    elif unit == "m":
        result *= 1000.0
    elif unit == "kg m-2 s-1":
        result *= source_seconds if spec.aggregation == "Sum" else seconds
    elif unit == "mm/day":
        result *= (source_seconds if spec.aggregation == "Sum" else seconds) / 86400.0
    elif unit == "J m-2":
        result /= seconds if spec.aggregation == "Sum" else source_seconds
    elif unit == "hPa":
        result *= 100.0
    elif unit == "kPa":
        result *= 1000.0
    elif unit == "km/h":
        result /= 3.6
    if spec.kind == "pet" and source_name in {"pev", "potential_evaporation"}:
        result *= -1
    if spec.kind in {"pre", "pet"} and (negative := result < 0).any():
        lowest = float(result[negative].min())
        if lowest < -0.01:  # a wrong unit, accumulation or sign, not round-off
            sign = " or the sign of the evaporation" if spec.kind == "pet" else ""
            raise ValueError(f"The input has negative amounts (down to {lowest:.3g} mm): check its unit, "
                             f"the cumulative setting{sign}.")
        if lowest < -1e-7 and not spec.clip_negative:  # float round-off below that is clipped silently
            raise NegativeAmounts(spec.kind, lowest)
        np.maximum(result, 0, out=result)
    return result

