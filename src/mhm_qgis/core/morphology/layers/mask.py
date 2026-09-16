"""Write the single-domain v6 morphology mask."""
from __future__ import annotations

import os
from pathlib import Path


def write_morph_mask(output_path, masked_derivatives, latlon_path) -> Path:
    """Write mask plus the exact L0 latitude and longitude arrays."""
    import numpy as np
    from netCDF4 import Dataset

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with Dataset(masked_derivatives) as source, Dataset(latlon_path) as coords:
            dem = source["dem"]
            rows, columns = dem.shape[-2:]
            lat = _coordinate(coords, "lat_l0", "lat", (rows, columns))
            lon = _coordinate(coords, "lon_l0", "lon", (rows, columns))
            with Dataset(temporary, "w", format="NETCDF4") as target:
                target.createDimension("y", rows)
                target.createDimension("x", columns)
                mask = target.createVariable("mask", "i1", ("y", "x"), zlib=True)
                latitude = target.createVariable("lat", "f8", ("y", "x"), zlib=True)
                longitude = target.createVariable("lon", "f8", ("y", "x"), zlib=True)
                nodata = getattr(dem, "_FillValue", getattr(dem, "missing_value", None))
                for start in range(0, rows, 256):
                    stop = min(rows, start + 256)
                    values = np.asarray(dem[start:stop, :])
                    valid = np.isfinite(values)
                    if nodata is not None:
                        valid &= values != nodata
                    mask[start:stop, :] = valid.astype("int8")
                    latitude[start:stop, :] = lat[start:stop, :]
                    longitude[start:stop, :] = lon[start:stop, :]
                mask.setncatts({"standard_name": "land_binary_mask", "units": "1"})
                latitude.setncatts({"standard_name": "latitude", "units": "degrees_north"})
                longitude.setncatts({"standard_name": "longitude", "units": "degrees_east"})
                target.Conventions = "CF-1.8"
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _coordinate(dataset, preferred, fallback, shape):
    variable = dataset.variables.get(preferred)
    if variable is None:
        variable = dataset.variables.get(fallback)
    if variable is None or variable.shape != shape:
        raise ValueError(f"latlon.nc has no {preferred} coordinate with shape {shape}.")
    return variable


__all__ = ["write_morph_mask"]
