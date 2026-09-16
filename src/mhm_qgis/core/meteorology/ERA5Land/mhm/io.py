"""NetCDF and mHM header writing."""
from __future__ import annotations

from pathlib import Path

from ....grid import header_from_coordinate_axes, write_header_file


def write_netcdf(ds, variable: str, output_file: Path, *, unlimited_dims=None) -> None:
    """Write one mHM forcing NetCDF with conservative CF/xarray encoding."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    encoding = {
        variable: {
            "dtype": "float64",
            "_FillValue": -9999.0,
            "missing_value": -9999.0,
            "zlib": True,
            "complevel": 4,
        },
        "time": {
            "dtype": "float64",
            "units": "seconds since 1900-01-01 00:00:00",
            "calendar": "proleptic_gregorian",
        },
        "time_bnds": {
            "dtype": "float64",
            "units": "seconds since 1900-01-01 00:00:00",
            "calendar": "proleptic_gregorian",
        },
        "lat": {
            "dtype": "float64",
            "_FillValue": None,
        },
        "lon": {
            "dtype": "float64",
            "_FillValue": None,
        },
    }

    last_error = None
    for engine in ("netcdf4", "h5netcdf", "scipy", None):
        try:
            engine_encoding = encoding
            if engine == "scipy":
                engine_encoding = _strip_compression_encoding(encoding)
            kwargs = {} if engine is None else {"engine": engine}
            ds_to_write = _strip_attrs_managed_by_encoding(
                ds, engine_encoding)
            ds_to_write.to_netcdf(
                output_file, encoding=engine_encoding, unlimited_dims=unlimited_dims, **kwargs)
            return
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Could not write NetCDF file {output_file}: {last_error}")


def write_header(ds, variable: str, header_file: Path, header: dict | None = None) -> None:
    """Write the mHM ASCII grid header that accompanies a forcing NetCDF."""
    if header is None:
        header = header_from_coordinate_axes(ds["lon"].values, ds["lat"].values)
    write_header_file(header_file, header)


def _strip_compression_encoding(encoding: dict) -> dict:
    stripped = {}
    for variable, options in encoding.items():
        stripped[variable] = {
            key: value
            for key, value in options.items()
            if key not in {"zlib", "complevel", "shuffle", "chunksizes"}
        }
    return stripped


def _strip_attrs_managed_by_encoding(ds, encoding: dict):
    """Remove attrs that xarray expects to own through encoding."""
    ds_to_write = ds.copy(deep=False)
    for variable, options in encoding.items():
        if variable not in ds_to_write.variables:
            continue
        for key in options:
            ds_to_write[variable].attrs.pop(key, None)
    return ds_to_write
