# -*- coding: utf-8 -*-
"""Widget state for `soil_multi_horizon_input.ui`.

The four component tabs (clay, sand, silt, bulk density) hold parallel row
lists; growing or shrinking the horizon count has to move all four together.
"""
from __future__ import annotations

from qgis.PyQt import QtWidgets

from ...core.handlers.lookup import MAX_SOIL_HORIZONS, SoilInput

def set_horizon_count(dialog, count: int) -> None:
    count = min(MAX_SOIL_HORIZONS, max(1, int(count)))
    dialog.spinBox_nSoilHorizons.setValue(count)
    dialog.tableWidget_soilHorizonDepthInputs.setRowCount(count)
    for component, parent_name, _, _, _, tab_name in dialog._COMPONENTS:
        rows = dialog._soil_rows[component]
        while len(rows) < count:
            rows.append(
                dialog._new_soil_row(
                    component,
                    len(rows) + 1,
                    getattr(dialog, parent_name),
                    getattr(dialog, tab_name),
                )
            )
        while len(rows) > count:
            dialog._delete_row(rows.pop())
    for index in range(count):
        dialog.tableWidget_soilHorizonDepthInputs.setVerticalHeaderItem(
            index, QtWidgets.QTableWidgetItem(f"Horizon {index + 1}")
        )


def set_input(dialog, value) -> None:
    data = value.as_dict() if isinstance(value, SoilInput) else value
    horizons = list(data.get("horizons", ()))
    if len(horizons) > MAX_SOIL_HORIZONS:
        raise ValueError(f"Soil supports at most {MAX_SOIL_HORIZONS} horizons.")
    set_horizon_count(dialog, len(horizons) or 1)
    dialog.comboBox_bulkDensityUnit.setCurrentText(
        str(data.get("bulk_density_unit", "") or "")
    )
    path_keys = {
        "clay": "clay_layer",
        "sand": "sand_layer",
        "silt": "silt_layer",
        "bulk_density": "bulk_density_layer",
    }
    for index, horizon in enumerate(horizons):
        dialog._set_cell(
            dialog.tableWidget_soilHorizonDepthInputs,
            index,
            0,
            horizon.get("upper_depth"),
        )
        dialog._set_cell(
            dialog.tableWidget_soilHorizonDepthInputs,
            index,
            1,
            horizon.get("lower_depth"),
        )
        for component, key in path_keys.items():
            dialog._select_path(
                dialog._soil_rows[component][index].adapter, horizon.get(key)
            )
