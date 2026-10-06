"""The v6 meteorology mask.

`config_input` names `meteo_mask_path`, and both mHM examples ship a
`meteo/mask.nc` holding an integer land mask on the forcing grid.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..grid import header_center_coordinates

NODATA = -9999


def write_meteo_mask(output_path, target_grid, valid=None) -> Path:
    """Write an integer mask on the L2 forcing grid.

    ``valid`` is an optional boolean array; without it every cell of the model
    extent counts as valid, which is what the padded L2 grid represents.
    """
    import numpy as np
    from netCDF4 import Dataset

    header: Mapping[str, Any] = target_grid.header
    rows, columns = int(header["nrows"]), int(header["ncols"])
    x_values, y_values = header_center_coordinates(header)
    if valid is None:
        values = np.ones((rows, columns), dtype="int32")
    else:
        values = np.where(np.asarray(valid, dtype=bool), 1, 0).astype("int32")
    if values.shape != (rows, columns):
        raise ValueError(
            f"The meteorology mask is {values.shape}, expected {(rows, columns)}."
        )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with Dataset(temporary, "w", format="NETCDF4") as handle:
            handle.createDimension("x", columns)
            handle.createDimension("y", rows)
            x = handle.createVariable("x", "f8", ("x",))
            x.setncatts({
                "axis": "X",
                "standard_name": "projection_x_coordinate",
                "long_name": "x coordinate",
                "units": "m",
            })
            x[:] = x_values
            y = handle.createVariable("y", "f8", ("y",))
            y.setncatts({
                "axis": "Y",
                "standard_name": "projection_y_coordinate",
                "long_name": "y coordinate",
                "units": "m",
            })
            y[:] = y_values
            mask = handle.createVariable(
                "mask", "i4", ("y", "x"), fill_value=NODATA)
            mask.setncatts({
                "standard_name": "land_binary_mask",
                "long_name": "meteorological mask",
                "units": "1",
            })
            mask[:, :] = values
            handle.Conventions = "CF-1.8"
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, output)
    return output


__all__ = ["write_meteo_mask"]
