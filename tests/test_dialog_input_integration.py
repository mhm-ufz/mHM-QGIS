"""Smoke tests for the plain input-combo integration."""

import json
import os
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mhm_qgis import standalone

standalone.install(force=True)

# isort: off
from qgis.core import (QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsVectorLayer)
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402

from mhm_qgis.qt.dialogs.input_selection import InputComboAdapter  # noqa: E402
from mhm_qgis.core.handlers.state import processing  # noqa: E402
from mhm_qgis.core.handlers.store.paths import master_data_folder
from mhm_qgis.core.handlers.store.layout import workspace_folder  # noqa: E402
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog  # noqa: E402
# isort: on

_APPLICATION = None


def _app():
    global _APPLICATION
    _APPLICATION = QApplication.instance() or _APPLICATION or QApplication([])
    return _APPLICATION


def test_dialog_prerequisites_keep_required_and_optional_input_checks(monkeypatch):
    from types import SimpleNamespace

    messages = []
    monkeypatch.setattr(
        "mhm_qgis.qt.dialogs.mhm_qgis_main.QMessageBox.critical",
        lambda _parent, _title, message: messages.append(message),
    )
    layers = {}
    dialog = SimpleNamespace(
        project_folder="",
        input_combo=lambda name: SimpleNamespace(currentLayer=lambda: layers.get(name)),
    )
    for folder, dem, points, needs_points, expected_message in (
        ("", None, None, False, "Please select a project folder before proceeding."),
        ("project", None, None, False, "Please select a DEM Raster Layer."),
        ("project", object(), None, True, "Please select a Pour Points Layer."),
        ("project", object(), None, False, None),
        ("project", object(), object(), True, None),
    ):
        dialog.project_folder = folder
        layers.update(dem=dem, pour_points=points)
        messages.clear()
        assert MhmQgisDialog.check_prerequisites(dialog, needs_points) is (expected_message is None)
        assert messages == ([] if expected_message is None else [expected_message])


def test_dialog_maps_new_combos_and_discovers_relative_project_files(tmp_path):
    _app()
    raster = tmp_path / "0 GIS" / "dem.tif"
    raster.parent.mkdir()
    raster.touch()
    excluded = tmp_path / "mhm-plugin" / "ignored.tif"
    excluded.parent.mkdir()
    excluded.touch()

    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog.checkBox_enableFolderSearch.setChecked(True)
    dialog.refresh_input_sources()

    assert isinstance(dialog.input_combo("dem"), InputComboAdapter)
    assert dialog.input_combo("dem").combo_box is dialog.comboBox_demInput
    labels = [
        dialog.comboBox_demInput.itemText(index)
        for index in range(dialog.comboBox_demInput.count())
    ]
    assert "0 GIS/dem.tif" in labels
    assert all("mhm-plugin" not in label for label in labels)
    dialog.close()


def test_project_without_saved_state_clears_previous_project_inputs(tmp_path):
    _app()
    first_project = tmp_path / "first"
    second_project = tmp_path / "second"
    first_project.mkdir()
    second_project.mkdir()
    raster = first_project / "soil.tif"
    raster.touch()

    dialog = MhmQgisDialog()
    dialog.project_folder = str(first_project)
    dialog.comboBox_soilInput.addItem(
        "soil.tif",
        {
            "origin": "file",
            "kind": "soil",
            "path": str(raster),
            "manual": True,
        },
    )
    dialog.comboBox_soilInput.setCurrentIndex(0)
    dialog.comboBox_soil_inputType.setCurrentIndex(1)
    dialog.comboBox_pourPointOutletID.addItem("old_id")
    dialog.comboBox_pourPointOutletID.setCurrentText("old_id")
    dialog.checkBox_DEMdomain.setChecked(True)
    dialog._categorical_lookup_configs["soil"] = {
        "lookup_table": str(first_project / "soil.csv"),
        "mapping_field": "source",
        "class_field": "class",
    }
    dialog.checkBox_enableFolderSearch.setChecked(True)

    dialog.project_folder = str(second_project)
    dialog.load_input_state()

    assert dialog.comboBox_soilInput.currentIndex() == -1
    assert dialog.comboBox_soil_inputType.currentIndex() == -1
    assert dialog.categorical_lookup_config("soil") is None
    assert not dialog.checkBox_enableFolderSearch.isChecked()
    assert dialog.selected_outlet_id_field() == ""
    assert not dialog.checkBox_DEMdomain.isChecked()
    dialog.close()


def test_pour_point_fields_and_domain_choice_round_trip(tmp_path):
    _app()
    pour_points = tmp_path / "pour_points.csv"
    pour_points.write_text(
        "station_id,outlet_name\n1,upper\n2,lower\n",
        encoding="utf-8",
    )
    layer = QgsVectorLayer(str(pour_points), "pour points", "delimitedtext")

    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog.input_combo("pour_points").setLayer(layer)

    fields = [
        dialog.comboBox_pourPointOutletID.itemText(index)
        for index in range(dialog.comboBox_pourPointOutletID.count())
    ]
    assert fields == ["", "station_id", "outlet_name"]
    assert dialog.selected_outlet_id_field() == "station_id"
    assert dialog.selected_outlet_ids() == ["1", "2"]

    dialog.comboBox_pourPointOutletID.setCurrentText("outlet_name")
    dialog.checkBox_DEMdomain.setChecked(True)
    dialog.save_input_state()
    dialog.close()

    state_path = Path(workspace_folder(tmp_path)) / "mhm_qgis_input_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pour_point_outlet_id_field"] == "outlet_name"
    assert state["dem_domain"] is True

    restored = MhmQgisDialog()
    restored.project_folder = str(tmp_path)
    restored.load_input_state()

    assert restored.selected_outlet_id_field() == "outlet_name"
    assert restored.selected_outlet_ids() == ["upper", "lower"]
    assert restored.checkBox_DEMdomain.isChecked()
    restored.close()


def test_meteo_folder_source_and_multiplier_state_round_trip(tmp_path):
    _app()
    precipitation = tmp_path / "forcing" / "pre"
    temperature = tmp_path / "forcing" / "temp"
    precipitation.mkdir(parents=True)
    temperature.mkdir()

    dialog = MhmQgisDialog()
    assert dialog.comboBox_precipitationFile.count() == 1
    assert dialog.comboBox_precipitationFile.itemText(0) == ""
    dialog.project_folder = str(tmp_path)
    dialog.refresh_meteo_folder_sources()
    for combo in (
        dialog.comboBox_precipitationFile,
        dialog.comboBox_temperatureFile,
        dialog.comboBox_petFile,
    ):
        assert combo.itemText(0) == ""
        assert combo.itemData(0) is None
    dialog._loading_input_state = True
    try:
        dialog.comboBox_precipitationFile.setCurrentIndex(
            dialog._folder_combo_index(
                dialog.comboBox_precipitationFile,
                str(precipitation),
            )
        )
        dialog.comboBox_temperatureFile.setCurrentIndex(
            dialog._folder_combo_index(
                dialog.comboBox_temperatureFile,
                str(temperature),
            )
        )
        dialog.comboBox_precipitationDataSource.setCurrentIndex(0)
        dialog.comboBox_temperatureDataSource.setCurrentIndex(1)
        dialog.spinBox_L2ResolutionMultiplier.setValue(4)
    finally:
        dialog._loading_input_state = False
    dialog.save_input_state()
    dialog.close()

    state_path = Path(workspace_folder(tmp_path)) / "mhm_qgis_input_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["meteo_inputs"]["precipitation"] == {
        "folder": "forcing/pre",
        "source": "mHM ready",
    }
    assert state["meteo_inputs"]["l2_multiplier"] == 4

    restored = MhmQgisDialog()
    restored.project_folder = str(tmp_path)
    restored.load_input_state()

    assert restored.selected_meteo_folder("precipitation") == str(
        precipitation.resolve()
    )
    assert restored.selected_meteo_source("precipitation") == "mhm_ready"
    assert restored.selected_meteo_source("temperature") == "era5land"
    assert restored.comboBox_petFile.currentIndex() == 0
    assert restored.selected_meteo_folder("pet") == ""
    assert restored.spinBox_L2ResolutionMultiplier.value() == 4
    restored.close()


def test_meteo_required_folders_and_optional_blank_pet(tmp_path):
    _app()
    precipitation = tmp_path / "pre"
    temperature = tmp_path / "temp"
    precipitation.mkdir()
    temperature.mkdir()

    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog.refresh_meteo_folder_sources()

    with pytest.raises(ValueError, match="precipitation data folder"):
        dialog.selected_meteo_specs()

    dialog._loading_input_state = True
    try:
        dialog.comboBox_precipitationFile.setCurrentIndex(
            dialog._folder_combo_index(
                dialog.comboBox_precipitationFile,
                str(precipitation),
            )
        )
        dialog.comboBox_precipitationDataSource.setCurrentIndex(0)
    finally:
        dialog._loading_input_state = False

    with pytest.raises(ValueError, match="temperature data folder"):
        dialog.selected_meteo_specs()

    dialog._loading_input_state = True
    try:
        dialog.comboBox_temperatureFile.setCurrentIndex(
            dialog._folder_combo_index(
                dialog.comboBox_temperatureFile,
                str(temperature),
            )
        )
        dialog.comboBox_temperatureDataSource.setCurrentIndex(0)
        dialog.comboBox_petFile.setCurrentIndex(0)
        dialog.comboBox_petDataSource.setCurrentIndex(1)
    finally:
        dialog._loading_input_state = False

    _, _, pet = dialog.selected_meteo_specs()
    assert pet is None
    dialog.close()


def test_combined_workflow_green_state_restores_from_workspace(tmp_path):
    _app()
    latlon = Path(master_data_folder(tmp_path)) / "latlon.nc"
    latlon.parent.mkdir(parents=True)
    latlon.touch()
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    processing.mark_workflow(
        tmp_path,
        "meteo_morph_setup",
        "completed",
    )
    dialog.close()

    restored = MhmQgisDialog()
    restored.project_folder = str(tmp_path)
    restored.refresh_morphology_workflow_button_states()

    assert "#2e7d32" in restored.pushButton_executeMeteoMorphSetup.styleSheet()
    restored.close()


def test_combined_workflow_green_state_requires_latlon(tmp_path):
    _app()
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    processing.mark_workflow(
        tmp_path,
        "meteo_morph_setup",
        "completed",
    )
    dialog.close()

    restored = MhmQgisDialog()
    restored.project_folder = str(tmp_path)
    restored.refresh_morphology_workflow_button_states()

    assert "#2e7d32" not in (
        restored.pushButton_executeMeteoMorphSetup.styleSheet()
    )
    assert not processing.workflow(tmp_path, "meteo_morph_setup")
    restored.close()


def test_precipitation_selection_displays_resolution_and_l0_multiplier(tmp_path):
    _app()
    folder = tmp_path / "forcing" / "pre"
    folder.mkdir(parents=True)
    dataset = xr.Dataset(
        {
            "pre": (
                ("time", "lat", "lon"),
                np.ones((1, 2, 2), dtype="float64"),
            )
        },
        coords={
            "time": np.asarray(["2001-01-01"], dtype="datetime64[ns]"),
            "lat": [51.0, 50.0],
            "lon": [10.0, 11.0],
        },
    )
    dataset.to_netcdf(folder / "pre.nc", engine="scipy")

    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog.mProjectionSelectionWidget_crs.setCrs(
        QgsCoordinateReferenceSystem("EPSG:4326")
    )
    dialog._grid_l0_info = {"resolution": 0.5, "unit": "deg"}
    dialog.refresh_meteo_folder_sources()
    dialog.comboBox_precipitationFile.setCurrentIndex(
        dialog._folder_combo_index(
            dialog.comboBox_precipitationFile,
            str(folder),
        )
    )
    dialog.comboBox_precipitationDataSource.setCurrentIndex(0)

    assert dialog.label_precipitationResolutionValue.text() == "1"
    assert dialog.label_precipitationResolutionUnit.text() == "deg"
    assert dialog.label_precipitationResolutionMultiplier.text() == "2.0"
    assert dialog.spinBox_L2ResolutionMultiplier.value() == 2

    dialog.spinBox_L2ResolutionMultiplier.setValue(3)
    dialog.inspect_meteo_selection("precipitation")
    assert dialog.spinBox_L2ResolutionMultiplier.value() == 3
    dialog.close()
