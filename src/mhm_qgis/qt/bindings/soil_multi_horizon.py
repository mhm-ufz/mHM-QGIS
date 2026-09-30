# -*- coding: utf-8 -*-
"""Widget wiring for `soil_multi_horizon_input.ui`."""
from __future__ import annotations

from ..controllers import soil_multi_horizon as controller


def bind_multi_horizon_soil(dialog) -> None:
    """Wire the horizon-count control."""
    # pushButton_addHorizonInputWidgets → controller.set_horizon_count
    dialog.pushButton_addHorizonInputWidgets.clicked.connect(
        lambda: controller.set_horizon_count(
            dialog, dialog.spinBox_nSoilHorizons.value()
        )
    )


__all__ = ["bind_multi_horizon_soil"]
