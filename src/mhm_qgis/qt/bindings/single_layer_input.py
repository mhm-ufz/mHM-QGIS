# -*- coding: utf-8 -*-
"""Widget wiring for `single_layer_input_with_lookup_config_dialog.ui`."""
from __future__ import annotations

from ..controllers import single_layer_input as controller
from ..dialogs.input_selection import _browse_raster_into_combo as browse

INPUT_FILTER = "Input layers (*.asc *.nc *.shp *.tif *.tiff);;All files (*)"


def bind_single_layer_lookup(dialog, title: str) -> None:
    """Wire the input layer, lookup table and their refresh."""
    dialog.lookup_table_combo.currentIndexChanged.connect(
        lambda *_args: controller._refresh_fields(dialog)
    )
    # pushButton_browseInputLayer → _browse_raster_into_combo
    dialog.pushButton_browseInputLayer.clicked.connect(
        lambda: browse(
            dialog,
            dialog.comboBox_inputLayer,
            f"Select {title} input",
            INPUT_FILTER,
        )
    )
    # pushButton_browseLookupTable → controller._browse_lookup
    dialog.pushButton_browseLookupTable.clicked.connect(
        lambda: controller._browse_lookup(dialog)
    )


__all__ = ["bind_single_layer_lookup"]
