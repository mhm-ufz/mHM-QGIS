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
from .file_picker import open_file

EXCLUDED_PROJECT_FOLDERS = {WORKSPACE_FOLDER_NAME}
INPUT_EXTENSIONS = {
    "dem": {".asc", ".nc", ".tif"},
    "pour_points": {".shp"},
    "land_cover": {".asc", ".nc", ".shp", ".tif"},
    "soil": {".asc", ".nc", ".shp", ".tif"},
    "geology": {".asc", ".nc", ".shp", ".tif"},
    "lai": {".asc", ".nc", ".shp", ".tif"},
    "lookup": {".csv", ".txt"},
}
_KIND_ALIASES = {"lc": "land_cover"}


@dataclass(frozen=True)
class InputItem:
    """One display label and its QComboBox item metadata."""

    label: str
    data: dict


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
    """Present a plain QComboBox of QGIS layers and project files as one layer source."""

    layerChanged = QtCore.pyqtSignal(object)

    def __init__(self, combo_box, kind: str, parent=None):
        super().__init__(parent or combo_box)
        self.combo_box = combo_box
        self.kind = _kind(kind)
        self._layers = {}  # Python owns unregistered layers; QVariant must not.
        self._file_layer = ((), None)  # The current file selection, opened once.
        self.combo_box.currentIndexChanged.connect(self._emit_layer_changed)

    def _emit_layer_changed(self, _index=None):
        self.layerChanged.emit(self.currentLayer())

    def currentLayer(self):
        """Return the selected loaded layer or an unregistered file-backed layer."""
        from qgis.PyQt import sip

        data = self.combo_box.currentData()
        if isinstance(data, dict):
            if data.get("origin") == "qgis":
                layer = _project_layer(data.get("layer_id")) or self._layers.get(data.get("layer_id"))
                return layer if layer is not None and not sip.isdeleted(layer) else None
            if data.get("origin") == "file":
                return self._opened(data.get("path"), str(data.get("variable", "")))
        if data is not None and hasattr(data, "type"):
            return data
        text = self.combo_box.currentText().strip()
        return self._opened(text) if os.path.isfile(text) else None

    def _opened(self, path, variable=""):
        """Open a file layer once per file version rather than on every call."""
        from qgis.PyQt import sip

        try:
            stat = os.stat(path)
        except (OSError, TypeError, ValueError):
            return None
        key = (path, variable, stat.st_mtime_ns, stat.st_size)
        cached_key, layer = self._file_layer
        if cached_key != key or layer is None or sip.isdeleted(layer):
            layer = _file_layer(path, self.kind, variable)
            self._file_layer = (key, layer)
        return layer

    def source_path(self):
        """Return the selected file path or loaded layer provider source."""
        data = self.combo_box.currentData()
        if isinstance(data, dict) and data.get("origin") == "file":
            path = data.get("path")
            return str(Path(path).expanduser().resolve()) if path else ""
        layer = self.currentLayer()
        return str(layer.source() or "") if layer is not None else ""

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
        target_source = layer.source()
        for index in range(self.combo_box.count()):
            data = self.combo_box.itemData(index)
            if not isinstance(data, dict):
                continue
            if (
                data.get("layer") is layer
                or (target_id and data.get("layer_id") == target_id)
                or (target_source and data.get("source") == target_source)
            ):
                self.combo_box.setCurrentIndex(index)
                return

        item = qgis_layer_item(layer, self.kind)
        if item is not None:
            self.addItem(item.label, item.data)
            self.combo_box.setCurrentIndex(self.combo_box.count() - 1)

    def addItem(self, text, userData=None):
        if isinstance(userData, dict) and "layer" in userData:
            userData = dict(userData)
            self._layers[userData["layer_id"]] = userData.pop("layer")
        self.combo_box.addItem(text, userData)

    def currentData(self):
        return self.combo_box.currentData()

    def setCurrentIndex(self, index):
        self.combo_box.setCurrentIndex(index)


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
    from .netcdf_asc_additional_input import RasterDetailsDialog

    prompt = RasterDetailsDialog(path, parent)
    return prompt.selected_config() if prompt.exec() == QtWidgets.QDialog.DialogCode.Accepted else None


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


__all__ = [
    "EXCLUDED_PROJECT_FOLDERS",
    "INPUT_EXTENSIONS",
    "InputComboAdapter",
    "InputItem",
    "LaiNetcdfConfig",
    "RasterInputConfig",
    "ReadyInputConfig",
    "SingleLayerLookupConfig",
    "loaded_qgis_items",
    "qgis_layer_item",
    "raster_details",
    "scan_project_inputs",
]
