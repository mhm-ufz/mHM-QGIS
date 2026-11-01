"""Focused tests for project-file and QGIS input selection helpers."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



# isort: off
pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.PyQt import QtWidgets

from mhm_qgis.qt.dialogs.input_selection import (
    InputComboAdapter,
    InputItem,
    RasterInputConfig,
    _ensure_raster_details,
    loaded_qgis_items,
)
from mhm_qgis.qt.dialogs.lai_netcdf_input import LaiNetcdfInputDialog
from mhm_qgis.qt.dialogs.mhm_ready_morph_input import MhmReadyInputDialog
from mhm_qgis.qt.dialogs.single_layer_input import SingleLayerInputDialog
from mhm_qgis.qt.dialogs import file_picker
# isort: on

_APPLICATION = None


def _app():
    global _APPLICATION
    _APPLICATION = (
        QtWidgets.QApplication.instance()
        or _APPLICATION
        or QtWidgets.QApplication([])
    )
    return _APPLICATION


def _touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def test_adapter_returns_file_layer_without_adding_it_to_project(tmp_path):
    _app()
    raster = _raster(tmp_path / "dem.tif")
    combo = QtWidgets.QComboBox()
    adapter = InputComboAdapter(combo, "dem")
    item = InputItem("dem.tif", {"origin": "file", "kind": "dem", "path": str(raster.resolve())})
    adapter.addItem(item.label, item.data)
    before = dict(QgsProject.instance().mapLayers())

    layer = adapter.currentLayer()

    assert isinstance(layer, QgsRasterLayer)
    assert layer.source() == str(raster)
    assert adapter.source_path() == str(raster.resolve())
    assert QgsProject.instance().mapLayers() == before


def test_adapter_opens_a_file_layer_once_per_file_version(tmp_path):
    import os

    _app()
    raster = _raster(tmp_path / "dem.tif")
    adapter = InputComboAdapter(QtWidgets.QComboBox(), "dem")
    item = InputItem("dem.tif", {"origin": "file", "kind": "dem", "path": str(raster.resolve())})
    adapter.addItem(item.label, item.data)

    first = adapter.currentLayer()
    assert adapter.currentLayer() is first
    stamp = raster.stat().st_mtime_ns + 1_000_000_000
    os.utime(raster, ns=(stamp, stamp))
    assert adapter.currentLayer() is not first


def test_the_meteorology_form_keeps_the_files_crs(tmp_path):
    from mhm_qgis.qt.controllers import meteo_var_details_input as controller
    from mhm_qgis.qt.dialogs.meteo_var_details_input import MeteoVarDetailsDialog

    _app()
    form = MeteoVarDetailsDialog("pre", str(tmp_path), {"folder": str(tmp_path), "crs": "EPSG:3035"})

    assert form.mProjectionSelectionWidget_crs.crs().authid() == "EPSG:3035"
    assert controller.validate(form)
    assert form.spec.crs == "EPSG:3035"


def test_a_file_layer_carries_the_crs_assigned_to_it(tmp_path):
    _app()
    raster = _raster(tmp_path / "dem.tif")  # no CRS of its own, like an ASC without .prj
    adapter = InputComboAdapter(QtWidgets.QComboBox(), "dem")
    adapter.addItem("dem.tif", {"origin": "file", "path": str(raster.resolve()), "source_crs": "EPSG:3035"})

    assert adapter.currentLayer().crs().authid() == "EPSG:3035"


def test_adapter_selects_loaded_layer_and_emits_layer(tmp_path):
    _app()
    vector_path = _points(tmp_path / "points.geojson")
    layer = QgsVectorLayer(str(vector_path), "Outlets", "ogr")
    layer.setCrs(QgsCoordinateReferenceSystem("EPSG:32645"))
    combo = QtWidgets.QComboBox()
    adapter = InputComboAdapter(combo, "pour_points")
    received = []
    adapter.layerChanged.connect(received.append)

    adapter.setLayer(layer)

    assert adapter.currentLayer() is layer
    assert adapter.source_path() == str(vector_path)
    assert combo.currentText() == "Outlets [EPSG:32645]"
    assert received[-1] is layer


def test_loaded_qgis_items_filter_type_and_include_crs(tmp_path):
    raster_path = _raster(tmp_path / "dem.tif")
    vector_path = _points(tmp_path / "points.geojson")
    raster = QgsRasterLayer(str(raster_path), "DEM")
    vector = QgsVectorLayer(str(vector_path), "Outlets", "ogr")
    raster.setCrs(QgsCoordinateReferenceSystem("EPSG:3035"))
    project = QgsProject()
    project.addMapLayer(vector)
    project.addMapLayer(raster)

    items = loaded_qgis_items("dem", project)

    assert [item.label for item in items] == ["DEM [EPSG:3035]"]
    assert items[0].data["layer"] is raster


def test_new_input_dialogs_set_titles_and_return_complete_paths(tmp_path):
    _app()
    raster = _touch(tmp_path / "soil.tif")
    definition = _touch(tmp_path / "soil_classdefinition.txt")
    lookup = tmp_path / "soil.csv"
    lookup.write_text("code,class_id\nA,1\n", encoding="utf-8")

    ready = MhmReadyInputDialog(
        tmp_path,
        "soil",
        initial={
            "input_path": str(raster),
            "classdefinition_path": str(definition),
        },
    )
    assert ready.groupBox_morphFileInput.title() == "Soil"
    assert ready.groupBox_classDefinitionInput.isChecked()
    assert ready.selected_config().classdefinition_path == str(definition.resolve())

    single = SingleLayerInputDialog(
        tmp_path,
        "soil",
        initial={
            "input_path": str(raster),
            "lookup_table": str(lookup),
            "mapping_field": "code",
            "class_field": "class_id",
        },
    )
    assert single.groupBox_inputLayerType.title() == "Soil"
    assert single.selected_config().input_path == str(raster.resolve())


def test_single_layer_dialog_uses_loaded_qgis_layer(tmp_path):
    _app()
    source = _raster(tmp_path / "loaded.tif")
    lookup = tmp_path / "soil.csv"
    lookup.write_text("code,class_id\nA,1\n", encoding="utf-8")
    layer = QgsRasterLayer(str(source), "Loaded soil")
    project = QgsProject.instance()
    project.addMapLayer(layer)
    try:
        dialog = SingleLayerInputDialog(tmp_path, "soil")
        dialog.comboBox_inputLayer.setCurrentIndex(
            dialog.comboBox_inputLayer.findText("Loaded soil [unknown CRS]")
        )
        dialog.lookup_table_combo.addItem(lookup.name, str(lookup.resolve()))  # as the browse button adds it
        dialog.lookup_table_combo.setCurrentIndex(dialog.lookup_table_combo.count() - 1)
        dialog.mapping_field_combo.setCurrentText("code")
        dialog.class_field_combo.setCurrentText("class_id")

        config = dialog.selected_config()
        assert config is not None
        assert config.input_path == str(source)

        # The config's path is all the scheduler needs to recover the layer.
        from mhm_qgis.qgis_bridge import layers

        assert layers.find_by_source(config.input_path) is layer
    finally:
        project.removeMapLayer(layer.id())


def test_raster_details_preserve_loaded_layer_identity(monkeypatch):
    from mhm_qgis.qt.dialogs import input_selection

    _app()
    combo = QtWidgets.QComboBox()
    combo.addItem("Loaded raster", {
        "origin": "qgis",
        "layer_id": "layer-1",
        "source": "/tmp/input.asc",
    })
    monkeypatch.setattr(
        input_selection,
        "raster_details",
        lambda _parent, path: RasterInputConfig(path, "Band1", "EPSG:3035"),
    )

    assert _ensure_raster_details(None, combo)
    assert combo.currentData() == {
        "origin": "qgis",
        "layer_id": "layer-1",
        "source": "/tmp/input.asc",
        "path": "/tmp/input.asc",
        "variable": "Band1",
        "source_crs": "EPSG:3035",
    }


def test_lookup_browse_accepts_qt_clicked_boolean(tmp_path, monkeypatch):
    from mhm_qgis.qt.controllers import single_layer_input

    _app()
    dialog = SingleLayerInputDialog(tmp_path, "soil")
    monkeypatch.setattr(single_layer_input, "_browse_into_combo", lambda *_args: None)

    single_layer_input._browse_lookup(dialog, False)


def test_file_picker_remembers_directory_per_dialog(tmp_path, monkeypatch):
    _app()
    outer = tmp_path / "project"
    chosen = tmp_path / "inputs"
    outer.mkdir()
    chosen.mkdir()
    selected_file = chosen / "soil.tif"
    selected_file.touch()
    values = {}

    class Settings:
        def value(self, key, default=""):
            return values.get(key, default)

        def setValue(self, key, value):
            values[key] = value

    starts = []
    picker = SimpleNamespace(
        getOpenFileName=lambda parent, title, start, file_filter: (
            starts.append(start) or str(selected_file),
            "",
        ),
        getExistingDirectory=lambda parent, title, start: (
            starts.append(start) or str(chosen)
        ),
    )
    monkeypatch.setattr(file_picker, "QSettings", Settings)
    monkeypatch.setattr(file_picker, "QFileDialog", picker)

    dialog = QtWidgets.QDialog()
    dialog.project_directory = str(outer)
    file_picker.open_file(dialog, "Input", "All files (*)")
    file_picker.open_directory(dialog, "Folder")

    class OtherDialog(QtWidgets.QDialog):
        pass

    other = OtherDialog()
    other.project_directory = str(outer)
    file_picker.open_directory(other, "Folder")

    assert starts == [str(outer), str(chosen), str(outer)]


def test_lai_netcdf_dialog_returns_temporal_choices(tmp_path):
    _app()
    source = _touch(tmp_path / "lai.nc")
    dialog = LaiNetcdfInputDialog(
        tmp_path,
        initial={
            "input_path": str(source),
            "target_timestep": "Monthly Gridded Data",
        },
    )

    assert dialog.selected_config().target_timestep == "Monthly Gridded Data"


def _raster(path):
    from osgeo import gdal
    dataset = gdal.GetDriverByName("GTiff").Create(str(path), 2, 2, 1)
    dataset.GetRasterBand(1).Fill(1)
    dataset = None
    return path


def _points(path):
    import json
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [0, 0]}}]}))
    return path
