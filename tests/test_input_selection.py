"""Focused tests for project-file and QGIS input selection helpers."""

from __future__ import annotations

import pytest

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



# isort: off
pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.core import (  # noqa: E402
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.PyQt import QtWidgets  # noqa: E402

from mhm_qgis.qt.dialogs.input_selection import (  # noqa: E402
    EXCLUDED_PROJECT_FOLDERS,
    InputComboAdapter,
    LaiNetcdfInputDialog,
    LookupConfigDialog,
    MhmReadyInputDialog,
    SingleLayerInputDialog,
    loaded_qgis_items,
    scan_project_folders,
    scan_project_inputs,
)
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


def test_scanner_filters_case_insensitively_and_prunes_only_owned_roots(tmp_path):
    wanted = _touch(tmp_path / "0 GIS" / "DEM.TIF")
    nested_named_data = _touch(tmp_path / "inputs" / "data" / "nested.asc")
    _touch(tmp_path / "0 GIS" / "ignore.shp")
    for folder in EXCLUDED_PROJECT_FOLDERS:
        _touch(tmp_path / folder / "excluded.tif")

    items = scan_project_inputs(tmp_path, "dem")

    assert [item.label for item in items] == [
        "0 GIS/DEM.TIF",
        "inputs/data/nested.asc",
    ]
    assert [item.data["path"] for item in items] == [
        str(wanted.resolve()),
        str(nested_named_data.resolve()),
    ]


def test_scanner_uses_input_specific_extensions(tmp_path):
    for filename in (
        "dem.nc",
        "grid.asc",
        "raster.tif",
        "points.shp",
        "table.csv",
        "table.txt",
    ):
        _touch(tmp_path / filename)

    assert {item.label for item in scan_project_inputs(tmp_path, "dem")} == {
        "dem.nc",
        "grid.asc",
        "raster.tif",
    }
    assert [item.label for item in scan_project_inputs(tmp_path, "pour_points")] == [
        "points.shp"
    ]
    assert {item.label for item in scan_project_inputs(tmp_path, "soil")} == {
        "dem.nc",
        "grid.asc",
        "points.shp",
        "raster.tif",
    }
    assert {item.label for item in scan_project_inputs(tmp_path, "lookup")} == {
        "table.csv",
        "table.txt",
    }


def test_folder_scanner_lists_nested_inputs_but_not_plugin_workspace(tmp_path):
    (tmp_path / "forcing" / "precipitation").mkdir(parents=True)
    (tmp_path / "forcing" / "temperature").mkdir()
    (tmp_path / "mhm-qgis" / "data" / "meteo").mkdir(parents=True)

    items = scan_project_folders(tmp_path)

    assert [item.label for item in items] == [
        "forcing",
        "forcing/precipitation",
        "forcing/temperature",
    ]
    assert all(
        Path(item.data["path"]).is_absolute()
        and item.data["origin"] == "folder"
        for item in items
    )


def test_adapter_returns_file_layer_without_adding_it_to_project(tmp_path):
    _app()
    raster = _raster(tmp_path / "dem.tif")
    combo = QtWidgets.QComboBox()
    adapter = InputComboAdapter(combo, "dem")
    item = scan_project_inputs(tmp_path, "dem")[0]
    adapter.addItem(item.label, item.data)
    before = dict(QgsProject.instance().mapLayers())

    layer = adapter.currentLayer()

    assert isinstance(layer, QgsRasterLayer)
    assert layer.source() == str(raster)
    assert adapter.source_path() == str(raster.resolve())
    assert QgsProject.instance().mapLayers() == before


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


def test_lookup_dialog_lists_tables_and_requires_both_fields(tmp_path):
    _app()
    table = tmp_path / "inputs" / "soil.txt"
    table.parent.mkdir(parents=True)
    table.write_text(
        "map_code\tclass_id\tname\t*ignored\n"
        "WR\t1\tRegosol\t10\nCM\t2\tCambisol\t20\n",
        encoding="utf-8",
    )
    _touch(tmp_path / "mhm-qgis" / "ignored.csv")

    dialog = LookupConfigDialog(tmp_path)

    assert dialog.lookup_table_combo.count() == 1
    assert dialog.lookup_table_combo.currentText() == "inputs/soil.txt"
    assert {
        dialog.mapping_field_combo.itemText(index)
        for index in range(dialog.mapping_field_combo.count())
    } == {"map_code", "class_id", "name"}
    assert dialog.selected_config() is None

    dialog.mapping_field_combo.setCurrentText("map_code")
    dialog.class_field_combo.setCurrentText("class_id")
    config = dialog.selected_config()

    assert config is not None
    assert config.lookup_table == str(table.resolve())
    assert config.mapping_field == "map_code"
    assert config.class_field == "class_id"


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
    assert ready.groupBox_inputType.title() == "Soil"
    assert ready.comboBox_classDefinitionInput.isEnabled()
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
