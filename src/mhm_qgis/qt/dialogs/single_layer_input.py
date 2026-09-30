"""Single categorical layer and lookup-table dialog."""

from pathlib import Path

from qgis.PyQt import QtWidgets

from ..bindings.single_layer_input import bind_single_layer_lookup
from ..controllers import single_layer_input as controller
from ..ui import load_form
from .input_selection import (
    InputComboAdapter,
    SingleLayerLookupConfig,
    _combo_path,
    _combo_raster_details,
    _ensure_raster_details,
    _populate_file_combo,
    _populate_layer_combo,
    _restore_raster_details,
)


class SingleLayerInputDialog(
    QtWidgets.QDialog,
    load_form("single_layer_input_with_lookup_config_dialog.ui"),
):
    def __init__(self, project_folder, kind, parent=None, initial=None):
        super().__init__(parent)
        self.setupUi(self)
        self.kind = str(kind)
        title = {
            "lc": "Land Use",
            "soil": "Soil",
            "geology": "Geology",
            "lai": "LAI",
        }[self.kind]
        self.groupBox_inputLayerType.setTitle(title)
        initial = initial or {}
        source_kind = "land_cover" if self.kind == "lc" else self.kind
        self.input_adapter = InputComboAdapter(
            self.comboBox_inputLayer, source_kind, self
        )
        _populate_layer_combo(
            self.input_adapter,
            project_folder,
            source_kind,
            initial.get("input_path", "") if isinstance(initial, dict) else "",
        )
        _restore_raster_details(self.comboBox_inputLayer, initial)
        _populate_file_combo(
            self.lookup_table_combo,
            project_folder,
            "lookup",
            initial.get("lookup_table", "") if isinstance(initial, dict) else "",
        )
        self._initial = initial
        bind_single_layer_lookup(self, title)
        controller._refresh_fields(self)

    def selected_config(self) -> SingleLayerLookupConfig | None:
        source = self.input_adapter.source_path()
        lookup = _combo_path(self.lookup_table_combo)
        mapping = self.mapping_field_combo.currentText().strip()
        class_field = self.class_field_combo.currentText().strip()
        if not source or not lookup or not mapping or not class_field:
            return None
        data = self.comboBox_inputLayer.currentData()
        qgis_source = isinstance(data, dict) and data.get("origin") == "qgis"
        layer = self.input_adapter.currentLayer() if qgis_source else None
        if not ((layer is not None and layer.isValid()) or Path(source).is_file()):
            return None
        if not Path(lookup).is_file():
            return None
        details = _combo_raster_details(self.comboBox_inputLayer)
        return SingleLayerLookupConfig(
            source,
            lookup,
            mapping,
            class_field,
            details.get("variable", ""),
            details.get("source_crs", ""),
        )

    def accept(self):
        source = self.input_adapter.source_path()
        if Path(source).suffix.lower() in {".asc", ".nc"} and not _ensure_raster_details(
            self, self.comboBox_inputLayer
        ):
            return
        if self.selected_config() is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Missing Input",
                "Select an input layer and lookup fields.",
            )
            return
        super().accept()


__all__ = ["SingleLayerInputDialog"]

