"""The multi-horizon soil tabs of the main form (`tabWidget_multiHorizonSoilInput`).

The four component tabs (clay, sand, silt, bulk density) hold parallel rows, one per
horizon; the horizon count moves all four together. Each tab's first row is Designer's.
"""
from __future__ import annotations

import os
from pathlib import Path

from qgis.PyQt import QtWidgets

from ...core.handlers.lookup import (
    MAX_SOIL_HORIZONS,
    SoilHorizon,
    SoilInput,
    validate_soil_input,
)
from ..dialogs.file_picker import open_file
from ..dialogs.input_selection import (
    INPUT_EXTENSIONS,
    VECTOR_EXTENSIONS,
    InputComboAdapter,
    loaded_qgis_items,
)

# component, Designer's first-horizon combo, browse button and row layout
COMPONENTS = (
    ("clay", "comboBox_inputClayLayerHorizon1", "pushButton_browsecClayInputHorizon1",
     "horizontalLayout_inputClayHorizon1"),
    ("sand", "comboBox_inputSandLayerHorizon1", "pushButton_browseSandInputHorizon1",
     "horizontalLayout_inputSandHorizon1"),
    ("silt", "comboBox_inputSiltLayerHorizon1", "pushButton_browseSiltInputHorizon1",
     "horizontalLayout_inputSiltHorizon1"),
    ("bulk_density", "comboBox_inputBulkDensityLayerHorizon1", "pushButton_browseBulkDensityInputHorizon1",
     "horizontalLayout_inputBulkDensityHorizon1"),
)


class SoilHorizonInputs:
    def __init__(self, dialog):
        self.dialog = dialog
        self.rows = {
            component: [(getattr(dialog, layout), self._adapter(getattr(dialog, combo), getattr(dialog, browse)))]
            for component, combo, browse, layout in COMPONENTS
        }
        dialog.spinBox_nSoilHorizons.setMaximum(MAX_SOIL_HORIZONS)
        # pushButton_addHorizonInputWidgets → set_count
        dialog.pushButton_addHorizonInputWidgets.clicked.connect(
            lambda: self.set_count(dialog.spinBox_nSoilHorizons.value()))
        self.set_count(1)

    def _adapter(self, combo, browse):
        adapter = InputComboAdapter(combo, "soil", self.dialog)
        for item in loaded_qgis_items("dem"):  # the rasters loaded in QGIS
            combo.addItem(item.label, item.data)
        combo.setCurrentIndex(-1)
        browse.clicked.connect(lambda _checked=False: self._browse(adapter))
        return adapter

    def _browse(self, adapter) -> None:
        patterns = " ".join(f"*{extension}" for extension in sorted(INPUT_EXTENSIONS["soil"] - VECTOR_EXTENSIONS))
        path, _ = open_file(self.dialog, "Select Raster Layer", f"Raster files ({patterns});;All files (*)")
        self.select_path(adapter, path)

    @staticmethod
    def select_path(adapter, path) -> None:
        if not path:
            return
        path = str(Path(path).expanduser().resolve())
        combo = adapter.combo_box
        for index in range(combo.count()):
            data = combo.itemData(index)
            if isinstance(data, dict) and data.get("path") == path:
                combo.setCurrentIndex(index)
                return
        combo.addItem(path, {"origin": "file", "kind": adapter.kind, "path": path, "manual": True})
        combo.setCurrentIndex(combo.count() - 1)

    def set_count(self, count: int) -> None:
        count = min(MAX_SOIL_HORIZONS, max(1, int(count)))
        table = self.dialog.tableWidget_soilHorizonDepthInputs
        self.dialog.spinBox_nSoilHorizons.setValue(count)
        table.setRowCount(count)
        for index in range(count):
            table.setVerticalHeaderItem(index, QtWidgets.QTableWidgetItem(f"Horizon {index + 1}"))
        for component, rows in self.rows.items():
            first_layout, first = rows[0]
            parent_layout, tab = first_layout.parent(), first.combo_box.parentWidget()
            while len(rows) < count:
                rows.append(self._new_row(component, len(rows) + 1, parent_layout, tab))
            while len(rows) > count:
                layout, adapter = rows.pop()
                while layout.count():
                    widget = layout.takeAt(0).widget()
                    if widget is not None:
                        widget.deleteLater()
                adapter.deleteLater()
                layout.deleteLater()

    def _new_row(self, component, number, parent_layout, tab):
        layout = QtWidgets.QHBoxLayout()
        combo, browse = QtWidgets.QComboBox(tab), QtWidgets.QPushButton("...", tab)
        browse.setMaximumWidth(40)
        for widget in (QtWidgets.QLabel(f"Horizon {number}:", tab), combo, browse):
            layout.addWidget(widget)
        layout.setObjectName(f"horizontalLayout_input{component.title().replace('_', '')}Horizon{number}")
        parent_layout.insertLayout(parent_layout.count() - 1, layout)  # before the spacer
        return layout, self._adapter(combo, browse)

    def set_input(self, value) -> None:
        data = value.as_dict() if isinstance(value, SoilInput) else value
        horizons = list(data.get("horizons", ()))
        if len(horizons) > MAX_SOIL_HORIZONS:
            raise ValueError(f"Soil supports at most {MAX_SOIL_HORIZONS} horizons.")
        dialog, table = self.dialog, self.dialog.tableWidget_soilHorizonDepthInputs
        self.set_count(len(horizons) or 1)
        dialog.comboBox_bulkDensityUnit.setCurrentText(str(data.get("bulk_density_unit", "") or ""))
        dialog.doubleSpinBox_compositionStepValue.setValue(float(data.get("composition_step", 5.0)))
        dialog.doubleSpinBox_bulkDensityStepValue.setValue(float(data.get("bulk_density_step", 0.1)))
        for index, horizon in enumerate(horizons):
            for column, key in enumerate(("upper_depth", "lower_depth")):
                table.setItem(index, column, QtWidgets.QTableWidgetItem(str(horizon.get(key, ""))))
            for component, rows in self.rows.items():
                self.select_path(rows[index][1], horizon.get(f"{component}_layer"))

    def selected_input(self) -> SoilInput:
        table = self.dialog.tableWidget_soilHorizonDepthInputs
        horizons = tuple(
            SoilHorizon(index + 1, _number(table, index, 0, "upper depth"), _number(table, index, 1, "lower depth"),
                        *(_local_file(self.rows[component][index][1]) for component in ("clay", "sand", "silt", "bulk_density")))
            for index in range(table.rowCount())
        )
        value = SoilInput(horizons, self.dialog.comboBox_bulkDensityUnit.currentText().strip(),
                          composition_step=self.dialog.doubleSpinBox_compositionStepValue.value(),
                          bulk_density_step=self.dialog.doubleSpinBox_bulkDensityStepValue.value())
        validate_soil_input(value)
        return value


def _number(table, row: int, column: int, label: str) -> float:
    item = table.item(row, column)
    try:
        return float(item.text().strip() if item is not None else "")
    except ValueError as error:
        raise ValueError(f"Enter a numeric {label} for horizon {row + 1}.") from error


def _local_file(adapter) -> Path:
    """The local raster file behind a row's selection (a NetCDF variable source names its file)."""
    source = str(adapter.source_path() or "").split("|", 1)[0].split("?", 1)[0].strip()
    if source.upper().startswith("NETCDF:"):
        value = source[len("NETCDF:"):]
        if value[:1] in ("\"", "'"):
            source = value.split(value[0], 2)[1]
        else:
            source = value if os.path.isfile(value) else value.rsplit(":", 1)[0]
    if not source or not os.path.isfile(source):
        raise ValueError("Select a readable local raster for every input row.")
    return Path(source).resolve()
