# -*- coding: utf-8 -*-
"""Widget wiring for `lai_netcdf_input_dialog.ui`."""
from __future__ import annotations

RASTER_FILTER = "Raster files (*.asc *.nc *.tif *.tiff);;All files (*)"
INPUT_FILTER = "Input layers (*.asc *.nc *.shp *.tif *.tiff);;All files (*)"
NETCDF_FILTER = "NetCDF (*.nc);;All files (*)"
CLASS_DEFINITION_FILTER = "Class definition (*.txt *.csv);;All files (*)"


def _browse():
    """Import lazily: `input_selection` imports this module at load time."""
    from ..dialogs.input_selection import _browse_raster_into_combo

    return _browse_raster_into_combo


def bind_lai_netcdf(dialog) -> None:
    """Wire the LAI NetCDF browse button."""
    browse = _browse()
    dialog.pushButton_browseLAINetCDFInput.clicked.connect(
        lambda: browse(
            dialog,
            dialog.comboBox_laiNetCDFInput,
            "Select LAI NetCDF",
            NETCDF_FILTER,
        )
    )


__all__ = ["bind_lai_netcdf"]
