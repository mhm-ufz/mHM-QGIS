"""Controllers for the historical land-use and multi-horizon soil forms."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from ...core.handlers.lookup import (
    LandUseInput,
    LandUsePeriod,
    MAX_LAND_USE_PERIODS,
    MAX_SOIL_HORIZONS,
    SoilHorizon,
    SoilInput,
    validate_land_use_input,
    validate_soil_input,
)
from .input_selection import (
    INPUT_EXTENSIONS,
    InputComboAdapter,
    loaded_qgis_items,
    scan_project_inputs,
)
from .file_picker import open_file


from qgis.PyQt import QtWidgets  # noqa: E402

from ...qt.controllers import land_use_historical as land_use_controller
from ...qt.controllers import soil_multi_horizon as soil_controller
from ...qt.bindings.land_use_historical import bind_historical_land_use
from ...qt.bindings.soil_multi_horizon import bind_multi_horizon_soil
from ..ui import load_form

Ui_LandUse = load_form("land_use_historical_input.ui")
Ui_Soil = load_form("soil_multi_horizon_input.ui")


@dataclass
class _LandUseRow:
    layout: object
    start_label: object
    end_label: object
    combo: object
    browse: object
    adapter: InputComboAdapter


@dataclass
class _SoilRow:
    layout: object
    combo: object
    browse: object
    adapter: InputComboAdapter


class _DynamicInputDialogMixin:
    """Shared path-combo and dynamic-row helpers."""

    def _replace_accept_handler(self, button_box) -> None:
        try:
            button_box.accepted.disconnect()
        except TypeError:
            pass
        button_box.accepted.connect(self._accept)

    def _new_adapter(self, combo, kind: str) -> InputComboAdapter:
        adapter = InputComboAdapter(combo, kind, self)
        cache = self.__dict__.setdefault("_raster_item_cache", {})
        if kind not in cache:
            cache[kind] = list(loaded_qgis_items("dem")) + [
                item
                for item in scan_project_inputs(self.project_folder, kind)
                if Path(item.data["path"]).suffix.lower() != ".shp"
            ]
        for item in cache[kind]:
            combo.addItem(item.label, item.data)
        combo.setCurrentIndex(-1)
        return adapter

    def _browse_raster(self, adapter: InputComboAdapter, kind: str) -> None:
        extensions = sorted(INPUT_EXTENSIONS[kind] - {".shp"})
        patterns = " ".join(f"*{extension}" for extension in extensions)
        path, _ = open_file(
            self,
            "Select Raster Layer",
            f"Raster files ({patterns});;All files (*)",
        )
        if path:
            self._select_path(adapter, path)

    def _select_path(self, adapter: InputComboAdapter, path) -> None:
        if not path:
            return
        path = str(Path(path).expanduser().resolve())
        combo = adapter.combo_box
        for index in range(combo.count()):
            data = combo.itemData(index)
            if isinstance(data, dict) and data.get("path") == path:
                combo.setCurrentIndex(index)
                return
        combo.addItem(
            self._relative_label(path),
            {"origin": "file", "kind": adapter.kind, "path": path, "manual": True},
        )
        combo.setCurrentIndex(combo.count() - 1)

    def _select_combo_path(self, combo, path) -> None:
        path = str(Path(path).expanduser().resolve())
        for index in range(combo.count()):
            if str(combo.itemData(index) or "") == path:
                combo.setCurrentIndex(index)
                return
        combo.addItem(self._relative_label(path), path)
        combo.setCurrentIndex(combo.count() - 1)

    def _relative_label(self, path: str) -> str:
        try:
            return Path(path).relative_to(self.project_folder).as_posix()
        except ValueError:
            return path

    @staticmethod
    def _delete_row(row) -> None:
        while row.layout.count():
            item = row.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        row.adapter.deleteLater()
        row.layout.deleteLater()

    @staticmethod
    def _cell_text(table, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text().strip() if item is not None else ""

    @staticmethod
    def _set_cell(table, row: int, column: int, value) -> None:
        table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))

    @staticmethod
    def _selected_file(adapter: InputComboAdapter) -> Path:
        source = str(adapter.source_path() or "").strip()
        path = _local_path(source)
        if path is None or not path.is_file():
            raise ValueError("Select a readable local raster for every input row.")
        return path.resolve()


class HistoricalLandUseDialog(_DynamicInputDialogMixin, QtWidgets.QDialog, Ui_LandUse):
    """Collect continuous land-use periods and one categorical lookup."""

    def __init__(self, project_folder, parent=None, initial=None, all_time=False):
        super().__init__(parent)
        Ui_LandUse.setupUi(self, self)
        self.setWindowTitle("Historical Land Use")
        self.project_folder = Path(project_folder).expanduser().resolve()
        self.spinBox_nLandUseLayers.setMaximum(MAX_LAND_USE_PERIODS)
        from ...core.handlers.state.nml_settings import load_settings
        from ...core.handlers.store.layout import project_version
        times = load_settings(project_folder).get("time", {})
        self.simulation_start = times.get("sim_start", "1900-01-01T00:00:00")
        self.simulation_end = times.get("sim_end", "2101-01-01T00:00:00")
        self.version = project_version(project_folder)
        self._rows: list[_LandUseRow] = []
        self._lookup_path = ""
        self._lookup_error = ""

        self._rows.append(self._first_row())
        land_use_controller._populate_lookup(self)
        bind_historical_land_use(self)
        self._replace_accept_handler(self.buttonBox)
        land_use_controller.set_layer_count(self, 1)
        if initial:
            land_use_controller.set_input(self, initial)
        if all_time:
            land_use_controller.set_all_time(self)
        land_use_controller._lookup_changed(self)

    def _first_row(self) -> _LandUseRow:
        adapter = self._new_adapter(self.comboBox_inputLandUseLayer1, "land_cover")
        # Row browse button → _browse_raster
        self.pushButton_browseLandUseLayer1.clicked.connect(
            lambda: self._browse_raster(adapter, "land_cover")
        )
        return _LandUseRow(
            self.horizontalLayout_inputLandUseLayer1,
            self.label_startingYearLayer1,
            self.label_endingYearLayer1,
            self.comboBox_inputLandUseLayer1,
            self.pushButton_browseLandUseLayer1,
            adapter,
        )

    def _new_land_use_row(self, number: int) -> _LandUseRow:
        layout = QtWidgets.QHBoxLayout()
        start = QtWidgets.QLabel(self.scrollAreaWidgetContents)
        separator = QtWidgets.QLabel("to", self.scrollAreaWidgetContents)
        end = QtWidgets.QLabel(self.scrollAreaWidgetContents)
        combo = QtWidgets.QComboBox(self.scrollAreaWidgetContents)
        browse = QtWidgets.QPushButton("...", self.scrollAreaWidgetContents)
        browse.setMaximumWidth(40)
        for widget in (start, separator, end, combo, browse):
            layout.addWidget(widget)
        for index, stretch in enumerate((2, 1, 2, 6, 1)):
            layout.setStretch(index, stretch)
        adapter = self._new_adapter(combo, "land_cover")
        # Dynamic row browse button → _browse_raster
        browse.clicked.connect(lambda: self._browse_raster(adapter, "land_cover"))
        layout.setObjectName(f"horizontalLayout_inputLandUseLayer{number}")
        self.verticalLayout_4.insertLayout(self.verticalLayout_4.count() - 1, layout)
        return _LandUseRow(layout, start, end, combo, browse, adapter)

    def selected_input(self) -> LandUseInput:
        if self._lookup_error:
            raise ValueError(self._lookup_error)
        periods = []
        for index, row in enumerate(self._rows):
            start, end = (self.tableWidget_landUseTimeInputs.cellWidget(index, column)
                          .dateTime().toString("yyyy-MM-ddTHH:mm:ss") for column in (0, 1))
            periods.append(LandUsePeriod(start, end, self._selected_file(row.adapter)))
        value = LandUseInput(
            tuple(periods),
            Path(self._lookup_path),
            self.comboBox_mappingFieldInput.currentText().strip(),
            self.comboBox_classFieldInput.currentText().strip(),
        )
        validate_land_use_input(value, simulation_start=self.simulation_start,
                                simulation_end=self.simulation_end, version=self.version)
        return value

    def _accept(self) -> None:
        try:
            self.selected_input()
        except ValueError as error:
            QtWidgets.QMessageBox.warning(self, "Invalid Land-use Input", str(error))
            return
        self.accept()

class MultiHorizonSoilDialog(_DynamicInputDialogMixin, QtWidgets.QDialog, Ui_Soil):
    """Collect clay, sand, silt and bulk-density layers by horizon."""

    _COMPONENTS = (
        (
            "clay",
            "verticalLayout_3",
            "horizontalLayout_inputClayHorizon1",
            "comboBox_inputClayLayerHorizon1",
            "pushButton_browsecClayInputHorizon1",
            "tab_clayInputs",
        ),
        (
            "sand",
            "verticalLayout_4",
            "horizontalLayout_inputSandHorizon1",
            "comboBox_inputSandLayerHorizon1",
            "pushButton_browseSandInputHorizon1",
            "tab_sandInputs",
        ),
        (
            "silt",
            "verticalLayout_5",
            "horizontalLayout_inputSiltHorizon1",
            "comboBox_inputSiltLayerHorizon1",
            "pushButton_browseSiltInputHorizon1",
            "tab_siltInputs",
        ),
        (
            "bulk_density",
            "verticalLayout_6",
            "horizontalLayout_inputBulkDensityHorizon1",
            "comboBox_inputBulkDensityLayerHorizon1",
            "pushButton_browseBulkDensityInputHorizon1",
            "tab_bulkDensityInputs",
        ),
    )

    def __init__(self, project_folder, parent=None, initial=None):
        super().__init__(parent)
        Ui_Soil.setupUi(self, self)
        self.setWindowTitle("Multi-horizon Soil Input")
        self.project_folder = Path(project_folder).expanduser().resolve()
        self.spinBox_nSoilHorizons.setMaximum(MAX_SOIL_HORIZONS)
        self._soil_rows: dict[str, list[_SoilRow]] = {}
        for component, _, layout_name, combo_name, browse_name, _ in self._COMPONENTS:
            adapter = self._new_adapter(getattr(self, combo_name), "soil")
            browse = getattr(self, browse_name)
            # First-horizon browse button → _browse_raster
            browse.clicked.connect(
                lambda _checked=False, item=adapter: self._browse_raster(item, "soil")
            )
            self._soil_rows[component] = [
                _SoilRow(
                    getattr(self, layout_name),
                    getattr(self, combo_name),
                    browse,
                    adapter,
                )
            ]
        bind_multi_horizon_soil(self)
        self._replace_accept_handler(self.buttonBox_cancelAndOK)
        soil_controller.set_horizon_count(self, 1)
        if initial:
            soil_controller.set_input(self, initial)

    def _new_soil_row(self, component, number, parent_layout, parent_widget):
        layout = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(f"Horizon {number}:", parent_widget)
        combo = QtWidgets.QComboBox(parent_widget)
        browse = QtWidgets.QPushButton("...", parent_widget)
        browse.setMaximumWidth(40)
        layout.addWidget(label)
        layout.addWidget(combo)
        layout.addWidget(browse)
        adapter = self._new_adapter(combo, "soil")
        # Dynamic horizon browse button → _browse_raster
        browse.clicked.connect(lambda: self._browse_raster(adapter, "soil"))
        layout.setObjectName(
            f"horizontalLayout_input{component.title().replace('_', '')}Horizon{number}"
        )
        parent_layout.insertLayout(parent_layout.count() - 1, layout)
        return _SoilRow(layout, combo, browse, adapter)

    def selected_input(self) -> SoilInput:
        horizons = []
        table = self.tableWidget_soilHorizonDepthInputs
        for index in range(table.rowCount()):
            horizons.append(
                SoilHorizon(
                    index + 1,
                    self._number_cell(table, index, 0, "upper depth"),
                    self._number_cell(table, index, 1, "lower depth"),
                    self._selected_file(self._soil_rows["clay"][index].adapter),
                    self._selected_file(self._soil_rows["sand"][index].adapter),
                    self._selected_file(self._soil_rows["silt"][index].adapter),
                    self._selected_file(self._soil_rows["bulk_density"][index].adapter),
                )
            )
        value = SoilInput(
            tuple(horizons), self.comboBox_bulkDensityUnit.currentText().strip()
        )
        validate_soil_input(value)
        return value

    def _accept(self) -> None:
        try:
            self.selected_input()
        except ValueError as error:
            QtWidgets.QMessageBox.warning(self, "Invalid Soil Input", str(error))
            return
        self.accept()

    @classmethod
    def _number_cell(cls, table, row: int, column: int, label: str) -> float:
        text = cls._cell_text(table, row, column)
        try:
            return float(text)
        except ValueError as error:
            raise ValueError(
                f"Enter a numeric {label} for horizon {row + 1}."
            ) from error


def _local_path(source: str) -> Path | None:
    source = source.split("|", 1)[0].split("?", 1)[0].strip()
    if source.upper().startswith("NETCDF:"):
        value = source[len("NETCDF:") :]
        if value.startswith(("\"", "'")):
            quote = value[0]
            end = value.find(quote, 1)
            source = value[1:end] if end > 0 else value.strip(quote)
        elif os.path.isfile(value):
            source = value
        elif ":" in value:
            source = value.rsplit(":", 1)[0]
    parsed = urlparse(source)
    if parsed.scheme == "file":
        source = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:/", source):
            source = source[1:]
    return Path(source).expanduser() if source else None


__all__ = ["HistoricalLandUseDialog", "MultiHorizonSoilDialog"]
