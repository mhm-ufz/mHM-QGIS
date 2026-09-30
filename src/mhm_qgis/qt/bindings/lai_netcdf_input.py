# -*- coding: utf-8 -*-
"""Widget wiring for `lai_netcdf_input_dialog.ui`."""
from __future__ import annotations

from ..dialogs.input_selection import _browse_raster_into_combo as browse

NETCDF_FILTER = "NetCDF (*.nc);;All files (*)"


def bind_lai_netcdf(dialog) -> None:
    """Wire the LAI NetCDF browse button."""
    # pushButton_browseLAINetCDFInput → _browse_raster_into_combo
    dialog.pushButton_browseLAINetCDFInput.clicked.connect(
        lambda: browse(
            dialog,
            dialog.comboBox_laiNetCDFInput,
            "Select LAI NetCDF",
            NETCDF_FILTER,
        )
    )


__all__ = ["bind_lai_netcdf"]
