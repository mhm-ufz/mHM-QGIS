"""Widget wiring for `land_use_historical_input.ui`.

Each row's browse button is connected where the row is built, since it closes
over that row's combo adapter.
"""
from __future__ import annotations

from ..controllers import land_use_historical as controller


def bind_historical_land_use(dialog) -> None:
    """Wire the row-count control, the period table and the lookup selector."""
    # pushButton_addLandUseInputWidgets → controller.set_layer_count
    dialog.pushButton_addLandUseInputWidgets.clicked.connect(
        lambda: controller.set_layer_count(
            dialog, dialog.spinBox_nLandUseLayers.value()
        )
    )
    dialog.tableWidget_landUseTimeInputs.cellChanged.connect(
        lambda *_args: controller._update_bounds(dialog)
    )
    dialog.comboBox_lookupTableInput.currentIndexChanged.connect(
        lambda *_args: controller._lookup_changed(dialog)
    )
    # pushButton_browsecLookupTable → controller._browse_lookup
    dialog.pushButton_browsecLookupTable.clicked.connect(
        lambda: controller._browse_lookup(dialog)
    )


__all__ = ["bind_historical_land_use"]
