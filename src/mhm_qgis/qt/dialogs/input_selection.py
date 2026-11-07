"""Input-combo helpers for QGIS layers and project files."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsMapLayer, QgsProject
from qgis.PyQt import QtCore, QtWidgets

from ...qgis_bridge import layers
from .file_picker import open_file

VECTOR_EXTENSIONS = {".shp", ".gpkg"}
INPUT_EXTENSIONS = {
    "dem": {".asc", ".nc", ".tif"},
    "pour_points": VECTOR_EXTENSIONS,
    "land_cover": {".asc", ".nc", ".tif", *VECTOR_EXTENSIONS},
    "soil": {".asc", ".nc", ".tif", *VECTOR_EXTENSIONS},
    "geology": {".asc", ".nc", ".tif", *VECTOR_EXTENSIONS},
    "lai": {".asc", ".nc", ".tif", *VECTOR_EXTENSIONS},
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
    layers: tuple = ()  # several (path, a, b): periods or horizon depths


@dataclass(frozen=True)
class SingleLayerLookupConfig:
    """A categorical source and its lookup-table configuration."""

    input_path: str
    lookup_table: str
    mapping_field: str
    class_field: str
    variable: str = ""
    source_crs: str = ""
    layers: tuple = ()  # several soil horizon layers (path, upper, lower) through the one lookup


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
    if kind == "pour_points":
        return layer_type == QgsMapLayer.LayerType.VectorLayer and is_point_layer(layer)
    if kind == "lookup":
        return layer_type == QgsMapLayer.LayerType.VectorLayer
    return layer_type in {QgsMapLayer.LayerType.RasterLayer, QgsMapLayer.LayerType.VectorLayer}


def is_point_layer(layer) -> bool:
    return layer is not None and layer.geometryType() == Qgis.GeometryType.Point


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
    except (RuntimeError, AttributeError):  # a deleted layer, or not a map layer
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
        except (RuntimeError, AttributeError):  # a deleted layer
            pass
    try:
        for layer in project.mapLayers().values():
            if _layer_id(layer) == layer_id:
                return layer
    except (RuntimeError, AttributeError):  # a deleted layer
        pass
    return None


def _file_layer(path: str, kind: str, variable: str = ""):
    path = str(path or "")
    if not path:
        return None
    vector = kind in {"pour_points", "lookup"} or Path(path.split("|", 1)[0]).suffix.lower() in VECTOR_EXTENSIONS
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
                return self._opened(data.get("path"), str(data.get("variable", "")), str(data.get("source_crs") or ""))
        if data is not None and hasattr(data, "type"):
            return data
        text = self.combo_box.currentText().strip()
        return self._opened(text) if os.path.isfile(text) else None

    def _opened(self, path, variable="", crs=""):
        """Open a file layer once per file version and assigned CRS rather than on every call."""
        from qgis.PyQt import sip

        try:
            stat = os.stat(str(path).split("|", 1)[0])  # a GeoPackage layer: `x.gpkg|layername=…`
        except (OSError, TypeError, ValueError):
            return None
        key = (path, variable, crs, stat.st_mtime_ns, stat.st_size)
        cached_key, layer = self._file_layer
        if cached_key != key or layer is None or sip.isdeleted(layer):
            layer = _file_layer(path, self.kind, variable)
            if layer is not None and crs:
                layer.setCrs(QgsCoordinateReferenceSystem(crs))  # an ASC has none; the user assigned it
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
    if initial and Path(initial).is_file():
        combo_box.addItem(str(Path(initial).resolve()), str(Path(initial).resolve()))
        selected = combo_box.count() - 1
    combo_box.setCurrentIndex(selected)


def _populate_layer_combo(adapter, project_folder, kind, initial=""):
    """Populate a categorical input with the layers loaded in QGIS."""
    combo_box = adapter.combo_box
    combo_box.clear()
    adapter.addItem("", {"origin": "empty"})
    selected = 0
    initial = str(initial or "")
    for item in loaded_qgis_items(kind):
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


def raster_draft(combo_box) -> dict:
    """A file combo's selection in the `initial` keys `_restore_raster_details` reads."""
    details = _combo_raster_details(combo_box)
    return {"input_path": _combo_path(combo_box), "variable": details.get("variable", ""),
            "source_crs": details.get("source_crs", "")}


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
    if Path(path).suffix.lower() in VECTOR_EXTENSIONS:
        path = _vector_layer(parent, path)
        crs = layers.crs_of(path, is_raster=False) if path else None
        return RasterInputConfig(path, "", layers.crs_string(crs) if crs is not None else "") if path else None
    if Path(path).suffix.lower() not in {".asc", ".nc"}:
        crs = layers.crs_of(path)
        return RasterInputConfig(
            path, "", layers.crs_string(crs) if crs is not None else ""
        )
    from .netcdf_asc_additional_input import RasterDetailsDialog

    prompt = RasterDetailsDialog(path, parent)
    return prompt.selected_config() if prompt.exec() == QtWidgets.QDialog.DialogCode.Accepted else None


def _vector_layer(parent, path):
    """`path`, or `path|layername=<layer>` for a GeoPackage, asking when it holds several."""
    if Path(path).suffix.lower() != ".gpkg":
        return path
    from osgeo import ogr

    dataset = ogr.Open(path)
    names = [dataset.GetLayer(index).GetName() for index in range(dataset.GetLayerCount())] if dataset else []
    if len(names) > 1:
        name, ok = QtWidgets.QInputDialog.getItem(
            parent, "GeoPackage layer", f"Layer of {Path(path).name}:", names, 0, False)
        if not ok:
            return None
    elif names:
        name = names[0]
    else:
        return path
    return f"{path}|layername={name}"


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
    current.update(
        path=details.input_path,
        variable=details.variable,
        source_crs=details.source_crs,
    )
    combo_box.setItemData(combo_box.currentIndex(), current)
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
    "INPUT_EXTENSIONS",
    "VECTOR_EXTENSIONS",
    "InputComboAdapter",
    "InputItem",
    "LaiNetcdfConfig",
    "RasterInputConfig",
    "ReadyInputConfig",
    "SingleLayerLookupConfig",
    "is_point_layer",
    "loaded_qgis_items",
    "qgis_layer_item",
    "raster_details",
    "raster_draft",
]
