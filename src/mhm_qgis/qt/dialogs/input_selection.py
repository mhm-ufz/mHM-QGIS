"""Input-combo helpers for QGIS layers and project files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from qgis.core import QgsMapLayer, QgsProject
from qgis.PyQt import QtCore, QtWidgets
from ...qgis_bridge import layers
from ...core.handlers.store.paths import WORKSPACE_FOLDER_NAME
from ...qt.controllers import single_layer_input as single_layer_controller
from ...qt.controllers import single_layer_lookup as lookup_controller
from ...qt.bindings.lai_netcdf_input import bind_lai_netcdf
from ...qt.bindings.mhm_ready_morph_input import bind_mhm_ready
from ...qt.bindings.netcdf_asc_additional_input import bind as bind_raster_details
from ...qt.bindings.single_layer_input import bind_single_layer_lookup
from ...qt.bindings.single_layer_lookup import bind_lookup_config
from ..ui import load_form
from .file_picker import open_file

Ui_LaiNetcdfInputDialog = load_form("lai_netcdf_input_dialog.ui")
Ui_MhmReadyInputDialog = load_form("mhm_ready_morph_input.ui")
Ui_RasterDetailsDialog = load_form("netcdf_asc_additional_input.ui")
Ui_SingleLayerLookupDialog = load_form("single_layer_input_with_lookup_config_dialog.ui")

EXCLUDED_PROJECT_FOLDERS = {WORKSPACE_FOLDER_NAME, "mhm-plugin"}
INPUT_EXTENSIONS = {
    "dem": {".asc", ".nc", ".tif"},
    "pour_points": {".shp"},
    "land_cover": {".asc", ".nc", ".shp", ".tif"},
    "soil": {".asc", ".nc", ".shp", ".tif"},
    "geology": {".asc", ".nc", ".shp", ".tif"},
    "lai": {".asc", ".nc", ".shp", ".tif"},
    "lookup": {".csv", ".txt"},
}
_KIND_ALIASES = {
    "pour_point": "pour_points",
    "landcover": "land_cover",
    "lc": "land_cover",
}


@dataclass(frozen=True)
class InputItem:
    """One display label and its QComboBox item metadata."""

    label: str
    data: dict


@dataclass(frozen=True)
class LookupConfig:
    """Selected lookup table and its two required fields."""

    lookup_table: str
    mapping_field: str
    class_field: str


@dataclass(frozen=True)
class ReadyInputConfig:
    """An already model-ready raster and optional class definition."""

    input_path: str
    classdefinition_path: str = ""
    variable: str = ""
    source_crs: str = ""


@dataclass(frozen=True)
class SingleLayerLookupConfig:
    """A categorical source and its lookup-table configuration."""

    input_path: str
    lookup_table: str
    mapping_field: str
    class_field: str
    variable: str = ""
    source_crs: str = ""


@dataclass(frozen=True)
class LaiNetcdfConfig:
    """LAI NetCDF source and output temporal resolution."""

    input_path: str
    target_timestep: str
    variable: str = ""
    source_crs: str = ""


@dataclass(frozen=True)
class RasterInputConfig:
    input_path: str
    variable: str = ""
    source_crs: str = ""


def _kind(value: str) -> str:
    kind = _KIND_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())
    if kind not in INPUT_EXTENSIONS:
        raise ValueError(f"Unknown input kind: {value!r}")
    return kind


def scan_project_inputs(project_folder, kind: str) -> list[InputItem]:
    """List eligible project files while pruning plugin-owned top-level folders."""
    project_root = Path(project_folder).expanduser().resolve()
    if not project_root.is_dir():
        return []

    kind = _kind(kind)
    extensions = INPUT_EXTENSIONS[kind]
    items = []
    for current_root, directories, filenames in os.walk(project_root):
        if Path(current_root) == project_root:
            directories[:] = [
                name
                for name in directories
                if name not in EXCLUDED_PROJECT_FOLDERS
            ]
        for filename in filenames:
            path = Path(current_root, filename)
            if path.suffix.lower() not in extensions:
                continue
            label = path.relative_to(project_root).as_posix()
            items.append(
                InputItem(
                    label,
                    {
                        "origin": "file",
                        "kind": kind,
                        "path": str(path.resolve()),
                    },
                )
            )
    return sorted(items, key=lambda item: item.label.casefold())


def scan_project_folders(project_folder) -> list[InputItem]:
    """List project subfolders while pruning the plugin-owned workspace."""
    project_root = Path(project_folder).expanduser().resolve()
    if not project_root.is_dir():
        return []

    items = []
    for current_root, directories, _ in os.walk(project_root):
        current_path = Path(current_root)
        if current_path == project_root:
            excluded = {name.casefold() for name in EXCLUDED_PROJECT_FOLDERS}
            directories[:] = [
                name for name in directories
                if name.casefold() not in excluded
            ]
        for dirname in directories:
            path = current_path / dirname
            items.append(
                InputItem(
                    path.relative_to(project_root).as_posix(),
                    {
                        "origin": "folder",
                        "path": str(path.resolve()),
                    },
                )
            )
    return sorted(items, key=lambda item: item.label.casefold())


def _layer_id(layer):
    getter = getattr(layer, "id", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


def _layer_type(layer):
    getter = getattr(layer, "type", None)
    try:
        return getter() if callable(getter) else None
    except Exception:
        return None


def _accepts_layer(kind: str, layer) -> bool:
    layer_type = _layer_type(layer)
    if kind == "dem":
        return layer_type == QgsMapLayer.LayerType.RasterLayer
    if kind in {"pour_points", "lookup"}:
        return layer_type == QgsMapLayer.LayerType.VectorLayer
    return layer_type in {QgsMapLayer.LayerType.RasterLayer, QgsMapLayer.LayerType.VectorLayer}


def qgis_layer_item(layer, kind: str) -> InputItem | None:
    """Build an item labelled ``name [CRS]`` for an eligible QGIS layer."""
    kind = _kind(kind)
    if layer is None or not _accepts_layer(kind, layer):
        return None
    try:
        if hasattr(layer, "isValid") and not layer.isValid():
            return None
    except Exception:
        return None

    try:
        name = layer.name() or "Unnamed layer"
    except Exception:
        name = "Unnamed layer"
    authid = ""
    try:
        crs = layer.crs()
        if crs is not None and crs.isValid():
            authid = crs.authid()
    except Exception:
        pass
    label = f"{name} [{authid or 'unknown CRS'}]"
    try:
        source = layer.source()
    except Exception:
        source = ""
    return InputItem(
        label,
        {
            "origin": "qgis",
            "kind": kind,
            "layer": layer,
            "layer_id": _layer_id(layer),
            "source": source,
        },
    )


def loaded_qgis_items(kind: str, project=None) -> list[InputItem]:
    """Return eligible layers currently loaded in a QGIS project."""
    project = project or QgsProject.instance()
    try:
        layers: Iterable = project.mapLayers().values()
    except Exception:
        layers = ()
    items = [qgis_layer_item(layer, kind) for layer in layers]
    return sorted(
        (item for item in items if item is not None),
        key=lambda item: item.label.casefold(),
    )


def _project_layer(layer_id):
    if not layer_id:
        return None
    project = QgsProject.instance()
    getter = getattr(project, "mapLayer", None)
    if callable(getter):
        try:
            layer = getter(layer_id)
            if layer is not None:
                return layer
        except Exception:
            pass
    try:
        for layer in project.mapLayers().values():
            if _layer_id(layer) == layer_id:
                return layer
    except Exception:
        pass
    return None


def _file_layer(path: str, kind: str, variable: str = ""):
    path = str(path or "")
    if not path:
        return None
    vector = kind in {"pour_points", "lookup"} or Path(path).suffix.lower() == ".shp"
    if variable and Path(path).suffix.lower() == ".nc":
        from ...core.handlers.file.netcdf.raster import source

        path = source(path, variable)
    physical = str(path).split('"', 2)[1] if str(path).startswith('NETCDF:"') else path
    return layers.open_layer(
        path, Path(physical).name, is_raster=not vector
    )


class InputComboAdapter(QtCore.QObject):
    """Present a plain QComboBox through the legacy layer-combo interface."""

    layerChanged = QtCore.pyqtSignal(object)

    def __init__(self, combo_box, kind: str, parent=None):
        super().__init__(parent or combo_box)
        self.combo_box = combo_box
        self.kind = _kind(kind)
        self.filters = None
        self.allow_empty = True
        self._layers = {}  # Python owns unregistered layers; QVariant must not.
        self.combo_box.currentIndexChanged.connect(self._emit_layer_changed)

    @property
    def currentIndexChanged(self):
        return self.combo_box.currentIndexChanged

    @property
    def currentTextChanged(self):
        return self.combo_box.currentTextChanged

    def _emit_layer_changed(self, _index=None):
        self.layerChanged.emit(self.currentLayer())

    def currentLayer(self):
        """Return the selected loaded layer or an unregistered file-backed layer."""
        data = self.combo_box.currentData()
        if isinstance(data, dict):
            if data.get("origin") == "qgis":
                from qgis.PyQt import sip
                layer = _project_layer(data.get("layer_id")) or self._layers.get(data.get("layer_id"))
                return layer if layer is not None and not sip.isdeleted(layer) else None
            if data.get("origin") == "file":
                return _file_layer(
                    data.get("path"), self.kind, str(data.get("variable", ""))
                )
        if data is not None and hasattr(data, "type"):
            return data
        text = self.combo_box.currentText().strip()
        if os.path.isfile(text):
            return _file_layer(text, self.kind)
        return None

    def source_path(self):
        """Return the selected file path or loaded layer provider source."""
        data = self.combo_box.currentData()
        if isinstance(data, dict):
            if data.get("origin") == "file":
                path = data.get("path")
                return str(Path(path).expanduser().resolve()) if path else ""
            if data.get("origin") == "qgis":
                layer = self.currentLayer()
                source = getattr(layer, "source", None)
                return str(source() if callable(source) else source or "")
        layer = self.currentLayer()
        source = getattr(layer, "source", None)
        return str(source() if callable(source) else source or "")

    def source_uri(self):
        """Return a GDAL-addressable source, including a NetCDF variable."""
        data = self.combo_box.currentData()
        if isinstance(data, dict) and data.get("origin") == "file":
            path = str(data.get("path") or "")
            variable = str(data.get("variable") or "")
            if path and variable and Path(path).suffix.lower() == ".nc":
                from ...core.handlers.file.netcdf.raster import source

                return source(path, variable)
            return path
        return self.source_path()

    def setLayer(self, layer):
        """Select an existing layer item, adding it when necessary."""
        if layer is None:
            self.combo_box.setCurrentIndex(-1)
            return

        target_id = _layer_id(layer)
        self._layers[target_id] = layer
        try:
            target_source = layer.source()
        except Exception:
            target_source = ""
        for index in range(self.combo_box.count()):
            data = self.combo_box.itemData(index)
            if not isinstance(data, dict):
                continue
            if data.get("layer") is layer:
                self.combo_box.setCurrentIndex(index)
                return
            if target_id and data.get("layer_id") == target_id:
                self.combo_box.setCurrentIndex(index)
                return
            if target_source and data.get("source") == target_source:
                self.combo_box.setCurrentIndex(index)
                return

        item = qgis_layer_item(layer, self.kind)
        if item is not None:
            self.addItem(item.label, item.data)
            self.combo_box.setCurrentIndex(self.combo_box.count() - 1)

    def setFilters(self, filters):
        self.filters = filters

    def setAllowEmptyLayer(self, allow_empty, text=""):
        self.allow_empty = bool(allow_empty)
        if self.allow_empty and text and self.combo_box.findText(text) < 0:
            self.combo_box.insertItem(0, text, {"origin": "empty"})

    def clear(self):
        self.combo_box.clear()

    def addItem(self, text, userData=None):
        if isinstance(userData, dict) and "layer" in userData:
            userData = dict(userData)
            self._layers[userData["layer_id"]] = userData.pop("layer")
        self.combo_box.addItem(text, userData)

    def addItems(self, texts):
        self.combo_box.addItems(texts)

    def count(self):
        return self.combo_box.count()

    def currentText(self):
        return self.combo_box.currentText()

    def setCurrentText(self, text):
        self.combo_box.setCurrentText(text)

    def currentIndex(self):
        return self.combo_box.currentIndex()

    def setCurrentIndex(self, index):
        self.combo_box.setCurrentIndex(index)

    def currentData(self):
        return self.combo_box.currentData()

    def findText(self, text):
        return self.combo_box.findText(text)

    def setEnabled(self, enabled):
        self.combo_box.setEnabled(enabled)

    def isEnabled(self):
        return self.combo_box.isEnabled()

    def setEditable(self, editable):
        self.combo_box.setEditable(editable)

    def setPlaceholderText(self, text):
        line_edit = self.combo_box.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText(text)


def _combo_path(combo_box) -> str:
    value = combo_box.currentData()
    if isinstance(value, dict):
        value = value.get("path") or value.get("source")
    return str(value or combo_box.currentText().strip())


def _populate_file_combo(combo_box, project_folder, kind, initial=""):
    combo_box.clear()
    combo_box.addItem("", "")
    selected = 0
    normalized = os.path.normcase(os.path.abspath(str(initial))) if initial else ""
    for item in scan_project_inputs(project_folder, kind):
        path = item.data["path"]
        combo_box.addItem(item.label, path)
        if normalized and os.path.normcase(os.path.abspath(path)) == normalized:
            selected = combo_box.count() - 1
    if initial and selected == 0 and Path(initial).is_file():
        combo_box.addItem(str(Path(initial).resolve()), str(Path(initial).resolve()))
        selected = combo_box.count() - 1
    combo_box.setCurrentIndex(selected)


def _populate_layer_combo(adapter, project_folder, kind, initial=""):
    """Populate a categorical input with loaded layers and project files."""
    combo_box = adapter.combo_box
    combo_box.clear()
    adapter.addItem("", {"origin": "empty"})
    selected = 0
    initial = str(initial or "")
    for item in (*loaded_qgis_items(kind), *scan_project_inputs(project_folder, kind)):
        adapter.addItem(item.label, item.data)
        source = str(item.data.get("source") or item.data.get("path") or "")
        if initial and source == initial:
            selected = combo_box.count() - 1
    if initial and selected == 0 and Path(initial).is_file():
        adapter.addItem(
            str(Path(initial).resolve()),
            {"origin": "file", "kind": kind, "path": str(Path(initial).resolve())},
        )
        selected = combo_box.count() - 1
    combo_box.setCurrentIndex(selected)


def _combo_raster_details(combo_box):
    value = combo_box.currentData()
    return dict(value) if isinstance(value, dict) else {}


def _restore_raster_details(combo_box, initial):
    if not isinstance(initial, dict) or not initial.get("input_path"):
        return
    index = combo_box.currentIndex()
    if index < 0:
        return
    data = combo_box.itemData(index)
    data = dict(data) if isinstance(data, dict) else {}
    data.update(
        origin="file",
        path=str(initial["input_path"]),
        variable=str(initial.get("variable", "")),
        source_crs=str(initial.get("source_crs", "")),
    )
    combo_box.setItemData(index, data)


def raster_details(parent, path):
    """Return file metadata, prompting only for NetCDF and ASCII rasters."""
    path = str(Path(path).resolve())
    if Path(path).suffix.lower() not in {".asc", ".nc"}:
        crs = layers.crs_of(path)
        return RasterInputConfig(
            path, "", layers.crs_string(crs) if crs is not None else ""
        )
    prompt = RasterDetailsDialog(path, parent)
    execute = getattr(prompt, "exec", None) or prompt.exec_
    return prompt.selected_config() if execute() == QtWidgets.QDialog.DialogCode.Accepted else None


def _ensure_raster_details(parent, combo_box):
    path = _combo_path(combo_box)
    if not path:
        return False
    current = _combo_raster_details(combo_box)
    if Path(path).suffix.lower() not in {".asc", ".nc"}:
        return True
    if current.get("variable") and current.get("source_crs"):
        return True
    details = raster_details(parent, path)
    if details is None:
        return False
    combo_box.setItemData(combo_box.currentIndex(), {
        "path": details.input_path,
        "variable": details.variable,
        "source_crs": details.source_crs,
    })
    return True


def _browse_raster_into_combo(parent, combo_box, title, file_filter):
    path, _ = open_file(parent, title, file_filter)
    if not path:
        return False
    details = raster_details(parent, path)
    if details is None:
        return False
    data = {
        "origin": "file",
        "path": details.input_path,
        "variable": details.variable,
        "source_crs": details.source_crs,
    }
    for index in range(combo_box.count()):
        if _combo_path_at(combo_box, index) == details.input_path:
            combo_box.setItemData(index, data)
            combo_box.setCurrentIndex(index)
            return True
    combo_box.addItem(details.input_path, data)
    combo_box.setCurrentIndex(combo_box.count() - 1)
    return True


def _browse_into_combo(parent, combo_box, title, file_filter):
    path, _ = open_file(parent, title, file_filter)
    if not path:
        return
    path = str(Path(path).resolve())
    for index in range(combo_box.count()):
        if _combo_path_at(combo_box, index) == path:
            combo_box.setCurrentIndex(index)
            return
    combo_box.addItem(path, path)
    combo_box.setCurrentIndex(combo_box.count() - 1)


def _combo_path_at(combo_box, index):
    value = combo_box.itemData(index)
    if isinstance(value, dict):
        value = value.get("path") or value.get("source")
    return str(value or "")


class LookupConfigDialog(QtWidgets.QDialog, Ui_SingleLayerLookupDialog):
    """Select a project lookup table, mapping field, and class field."""

    def __init__(self, project_folder, parent=None, initial=None):
        super().__init__(parent)
        self.setupUi(self)
        self._initial = initial
        self.groupBox_inputLayerType.hide()

        for item in scan_project_inputs(project_folder, "lookup"):
            self.lookup_table_combo.addItem(item.label, item.data["path"])

        bind_lookup_config(self)
        initial_path = self._initial_value("lookup_table")
        if initial_path:
            for index in range(self.lookup_table_combo.count()):
                if self.lookup_table_combo.itemData(index) == str(initial_path):
                    self.lookup_table_combo.setCurrentIndex(index)
                    break
        self._refresh_fields()

    def _initial_value(self, name):
        if isinstance(self._initial, dict):
            return self._initial.get(name)
        return getattr(self._initial, name, None)

    def _refresh_fields(self, *args, **kwargs):
        return lookup_controller._refresh_fields(self, *args, **kwargs)

    def _update_ok(self, *args, **kwargs):
        return lookup_controller._update_ok(self, *args, **kwargs)

    def selected_config(self) -> LookupConfig | None:
        path = self.lookup_table_combo.currentData()
        mapping_field = self.mapping_field_combo.currentText().strip()
        class_field = self.class_field_combo.currentText().strip()
        if not path or not mapping_field or not class_field:
            return None
        return LookupConfig(str(path), mapping_field, class_field)

    def accept(self):
        if self.selected_config() is not None:
            super().accept()


class RasterDetailsDialog(QtWidgets.QDialog, Ui_RasterDetailsDialog):
    """Select one raster variable and confirm its source CRS."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.setWindowTitle(f"Raster input: {Path(path).name}")
        bind_raster_details(self)
        from ..controllers import netcdf_asc_additional_input as controller

        controller.initialize(self, path)

    def refresh_crs(self, *args):
        from ..controllers import netcdf_asc_additional_input as controller

        controller.refresh_crs(self, *args)

    def selected_config(self):
        variable = self.comboBox_varName.currentText().strip()
        crs = self.mProjectionSelectionWidget_crsFileInput.crs()
        if not variable or crs is None or not crs.isValid():
            return None
        return RasterInputConfig(
            self.path,
            variable,
            layers.crs_string(crs),
        )

    def accept(self):
        if self.selected_config() is None:
            QtWidgets.QMessageBox.warning(
                self, "Missing Raster Metadata", "Select a variable and a valid CRS."
            )
            return
        super().accept()


class MhmReadyInputDialog(QtWidgets.QDialog, Ui_MhmReadyInputDialog):
    """Select an mHM-ready raster and its required support file."""

    def __init__(self, project_folder, kind, parent=None, initial=None):
        super().__init__(parent)
        self.setupUi(self)
        self.kind = str(kind)
        title = {
            "lc": "Land Use", "soil": "Soil", "geology": "Geology", "lai": "LAI"
        }[self.kind]
        self.groupBox_morphFileInput.setTitle(title)
        initial = initial or {}
        _populate_file_combo(
            self.comboBox_inputFile,
            project_folder,
            "land_cover" if self.kind == "lc" else self.kind,
            initial.get("input_path", "") if isinstance(initial, dict) else "",
        )
        _restore_raster_details(self.comboBox_inputFile, initial)
        self.groupBox_classDefinitionInput.setChecked(
            bool(initial.get("classdefinition_path"))
        )
        _populate_file_combo(
            self.comboBox_classDefinitionInputFile,
            project_folder,
            "lookup",
            initial.get("classdefinition_path", "") if isinstance(initial, dict) else "",
        )
        bind_mhm_ready(self, title)

    def selected_config(self) -> ReadyInputConfig | None:
        source = _combo_path(self.comboBox_inputFile)
        definition = (
            _combo_path(self.comboBox_classDefinitionInputFile)
            if self.groupBox_classDefinitionInput.isChecked()
            else ""
        )
        if not source or not Path(source).is_file():
            return None
        if definition and not Path(definition).is_file():
            return None
        details = _combo_raster_details(self.comboBox_inputFile)
        return ReadyInputConfig(
            source,
            definition,
            details.get("variable", ""),
            details.get("source_crs", ""),
        )

    def accept(self):
        if not _ensure_raster_details(self, self.comboBox_inputFile):
            return
        if self.selected_config() is None:
            QtWidgets.QMessageBox.warning(self, "Missing Input", "Select all required input files.")
            return
        super().accept()


class SingleLayerInputDialog(QtWidgets.QDialog, Ui_SingleLayerLookupDialog):
    """Select one categorical layer and its lookup configuration."""

    def __init__(
        self, project_folder, kind, parent=None, initial=None, initial_layer=None
    ):
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
        if initial_layer is not None:
            self.input_adapter.setLayer(initial_layer)
        _populate_file_combo(
            self.lookup_table_combo,
            project_folder,
            "lookup",
            initial.get("lookup_table", "") if isinstance(initial, dict) else "",
        )
        self._initial = initial
        bind_single_layer_lookup(self, title)
        self._refresh_fields()

    def _browse_lookup(self, *args, **kwargs):
        return single_layer_controller._browse_lookup(self, *args, **kwargs)

    def _refresh_fields(self, *args, **kwargs):
        return single_layer_controller._refresh_fields(self, *args, **kwargs)

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
            QtWidgets.QMessageBox.warning(self, "Missing Input", "Select an input layer and lookup fields.")
            return
        super().accept()


class LaiNetcdfInputDialog(QtWidgets.QDialog, Ui_LaiNetcdfInputDialog):
    """Select a NetCDF LAI source and temporal conversion."""

    def __init__(self, project_folder, parent=None, initial=None):
        super().__init__(parent)
        self.setupUi(self)
        initial = initial or {}
        _populate_file_combo(
            self.comboBox_laiNetCDFInput,
            project_folder,
            "lai",
            initial.get("input_path", "") if isinstance(initial, dict) else "",
        )
        _restore_raster_details(self.comboBox_laiNetCDFInput, initial)
        index = self.comboBox_timestepLAIInput.findText(
            str(initial.get("target_timestep", ""))
        )
        self.comboBox_timestepLAIInput.setCurrentIndex(index)
        bind_lai_netcdf(self)

    def selected_config(self) -> LaiNetcdfConfig | None:
        source = _combo_path(self.comboBox_laiNetCDFInput)
        target = self.comboBox_timestepLAIInput.currentText().strip()
        if not source or Path(source).suffix.lower() != ".nc" or not Path(source).is_file():
            return None
        if not target:
            return None
        details = _combo_raster_details(self.comboBox_laiNetCDFInput)
        return LaiNetcdfConfig(
            source,
            target,
            details.get("variable", ""),
            details.get("source_crs", ""),
        )

    def accept(self):
        if not _ensure_raster_details(self, self.comboBox_laiNetCDFInput):
            return
        if self.selected_config() is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Missing Input",
                "Select a NetCDF file and output temporal resolution.",
            )
            return
        super().accept()


__all__ = [
    "EXCLUDED_PROJECT_FOLDERS",
    "INPUT_EXTENSIONS",
    "InputComboAdapter",
    "InputItem",
    "LookupConfig",
    "LookupConfigDialog",
    "LaiNetcdfConfig",
    "LaiNetcdfInputDialog",
    "MhmReadyInputDialog",
    "RasterDetailsDialog",
    "RasterInputConfig",
    "ReadyInputConfig",
    "SingleLayerInputDialog",
    "SingleLayerLookupConfig",
    "loaded_qgis_items",
    "qgis_layer_item",
    "raster_details",
    "scan_project_inputs",
]
