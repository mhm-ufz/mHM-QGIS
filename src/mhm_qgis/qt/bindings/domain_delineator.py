# -*- coding: utf-8 -*-
"""Widget wiring for `domain_delineator_dialog.ui`."""
from __future__ import annotations


def bind(dialog) -> None:
    """Wire the outlet list and its action buttons.

    `finished` and the map tool's `canvasClicked` stay in the dialog: one is the
    dialog's own lifecycle, the other belongs to a tool built while picking.
    """
    dialog.listWidget_outlets.currentTextChanged.connect(dialog._load_outlet)
    dialog.checkBox_isGaugedOutlet.toggled.connect(dialog._set_discharge_enabled)
    # pushButton_browseDischargeFile → dialog._browse_discharge
    dialog.pushButton_browseDischargeFile.clicked.connect(dialog._browse_discharge)
    # pushButton_generateChannelNetwork → dialog._generate_channel_network
    dialog.pushButton_generateChannelNetwork.clicked.connect(
        dialog._generate_channel_network
    )
    # pushButton_pickLocation → dialog._start_picking
    dialog.pushButton_pickLocation.clicked.connect(dialog._start_picking)
    # pushButton_showDelineation → dialog._show_delineation
    dialog.pushButton_showDelineation.clicked.connect(dialog._show_delineation)
    # pushButton_nextPourPoint → dialog._next_outlet
    dialog.pushButton_nextPourPoint.clicked.connect(dialog._next_outlet)
    # pushButton_save → dialog._save_outlet
    dialog.pushButton_save.clicked.connect(dialog._save_outlet)
    # pushButton_close → dialog.reject
    dialog.pushButton_close.clicked.connect(dialog.reject)


__all__ = ["bind"]
