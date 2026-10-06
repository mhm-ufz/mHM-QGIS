"""Validation for reusing existing mHM forcing outputs."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .logging import log_message

EXPECTED_COORDINATE_ATTRS = {
    "lat": {
        "units": "degrees_north",
        "standard_name": "latitude",
        "axis": "Y",
    },
    "lon": {
        "units": "degrees_east",
        "standard_name": "longitude",
        "axis": "X",
    },
}


def existing_output_is_valid(
    output_file: Path,
    variable: str,
    np,
    xr,
    log: Callable[[str], None] | None,
) -> bool:
    """Return True only when an existing mHM forcing file has clean axes."""
    try:
        with xr.open_dataset(output_file, decode_times=False) as ds:
            if variable not in ds.data_vars:
                log_message(log, f"{variable}: existing file is missing variable {variable}.")
                return False

            for axis in ("lat", "lon"):
                if axis not in ds.coords:
                    log_message(log, f"{variable}: existing file is missing coordinate {axis}.")
                    return False
                values = np.asarray(ds[axis].values)
                if values.ndim != 1:
                    log_message(log, f"{variable}: existing coordinate {axis} is not one-dimensional.")
                    return False
                if len(np.unique(values)) != values.size:
                    log_message(log, f"{variable}: existing coordinate {axis} has duplicate values.")
                    return False

            for axis, attrs in EXPECTED_COORDINATE_ATTRS.items():
                if "_FillValue" in ds[axis].attrs:
                    log_message(
                        log,
                        f"{variable}: existing coordinate {axis} has an unnecessary _FillValue.",
                    )
                    return False
                for key, expected_value in attrs.items():
                    actual_value = str(ds[axis].attrs.get(key, ""))
                    if actual_value != expected_value:
                        log_message(
                            log,
                            f"{variable}: existing coordinate {axis} is missing {key}={expected_value}.",
                        )
                        return False
    except Exception as e:
        log_message(log, f"{variable}: could not validate existing output {output_file}: {e}")
        return False

    return True
