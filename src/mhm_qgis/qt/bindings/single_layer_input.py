# -*- coding: utf-8 -*-
"""Widget wiring for `single_layer_input_with_lookup_config_dialog.ui`."""
from __future__ import annotations

RASTER_FILTER = "Raster files (*.asc *.nc *.tif *.tiff);;All files (*)"
INPUT_FILTER = "Input layers (*.asc *.nc *.shp *.tif *.tiff);;All files (*)"
NETCDF_FILTER = "NetCDF (*.nc);;All files (*)"
CLASS_DEFINITION_FILTER = "Class definition (*.txt *.csv);;All files (*)"


def _browse():
    """Import lazily: `input_selection` imports this module at load time."""
    from ..dialogs.input_selection import _browse_raster_into_combo

    return _browse_raster_into_combo


def bind_single_layer_lookup(dialog, title: str) -> None:
    """Wire the input layer, lookup table and their refresh."""
    browse = _browse()
    dialog.lookup_table_combo.currentIndexChanged.connect(dialog._refresh_fields)
    dialog.pushButton_browseInputLayer.clicked.connect(
        lambda: browse(
            dialog,
            dialog.comboBox_inputLayer,
            f"Select {title} input",
            INPUT_FILTER,
        )
    )
    dialog.pushButton_browseLookupTable.clicked.connect(dialog._browse_lookup)


__all__ = ["bind_single_layer_lookup"]
