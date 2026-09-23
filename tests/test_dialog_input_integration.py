"""Smoke tests for the plain input-combo integration."""

import json
import os
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



# isort: off
pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.core import (QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsVectorLayer)
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402

from mhm_qgis.qt.dialogs.input_selection import InputComboAdapter  # noqa: E402
from mhm_qgis.core.handlers.state import processing  # noqa: E402
from mhm_qgis.core.handlers.store.paths import data_folder
from mhm_qgis.core.handlers.store.layout import input_state_path  # noqa: E402
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
    excluded = tmp_path / "mhm-qgis" / "ignored.tif"
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
    assert all("mhm-qgis" not in label for label in labels)
    dialog.close()


def test_main_configuration_buttons_use_dialog_first_controller(monkeypatch):
    from mhm_qgis.qt.controllers import main

    _app()
    calls = []
    for name in ("edit_namelists", "run_mhm", "handle_version_changed"):
        monkeypatch.setattr(
            main, name, lambda owner, action=name: calls.append((action, owner)),
        )
    dialog = MhmQgisDialog()
    calls.clear()
    dialog.pushButton_edit_nmls.click()
    dialog.pushButton_execute_mHM.click()
    dialog.comboBox_mHMversion.setCurrentIndex(
        (dialog.comboBox_mHMversion.currentIndex() + 1) % dialog.comboBox_mHMversion.count()
    )
    assert calls == [(name, dialog) for name in (
        "edit_namelists", "run_mhm", "handle_version_changed",
    )]
    assert not hasattr(dialog, "configuration_processor")
    dialog.close()


def test_morphology_display_wiring_selects_historical_band(tmp_path, monkeypatch):
    from qgis.PyQt.QtCore import QDate
    from mhm_qgis.core.handlers.state.nml_settings import update_section
    from mhm_qgis.qgis_bridge.display import morphology
    from mhm_qgis.qt.controllers import main

    _app()
    output = tmp_path / "lc_periods.nc"
    output.touch()
    update_section(tmp_path, "land_cover", {
        "output_path": str(output),
        "scenes": [
            {"start_year": 1990, "end_year": 1999},
            {"start_year": 2000, "end_year": 2010},
        ],
    })
    rendered, warnings = [], []
    monkeypatch.setattr(
        morphology, "show_raster",
        lambda owner, path, name, **options: rendered.append((path, options)),
    )
    monkeypatch.setattr(
        main.QMessageBox, "warning", lambda *args: warnings.append(args[-1]),
    )
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    combo = dialog.comboBox_morphVariableToDisplay
    editor = dialog.dateTimeEdit_forHistoricalInputs
    combo.setCurrentIndex(combo.findData("land_cover"))
    assert editor.isEnabled()
    assert editor.minimumDate() == QDate(1990, 1, 1)
    assert editor.maximumDate() == QDate(2010, 12, 31)
    editor.setDate(QDate(2005, 1, 1))
    dialog.pushButton_morphLayerDisplay.click()
    assert rendered == [(output, {
        "variable": "land_cover", "band": 2, "is_raster": True,
    })]
    combo.setCurrentIndex(combo.findData("dem"))
    assert not editor.isEnabled()
    dialog.pushButton_morphLayerDisplay.click()
    assert warnings == ["The selected morphology output has not been prepared yet."]
    assert len(rendered) == 1
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
    dialog.comboBox_domainDefinitionType.setCurrentIndex(0)
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
    assert dialog.comboBox_domainDefinitionType.currentIndex() == -1
    dialog.close()


def test_pour_point_fields_and_domain_choice_round_trip(tmp_path):
    _app()
    pour_points = tmp_path / "pour_points.geojson"
    pour_points.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"station_id": i, "outlet_name": name},
         "geometry": {"type": "Point", "coordinates": [i, 0]}}
        for i, name in ((1, "upper"), (2, "lower"))]}))
    layer = QgsVectorLayer(str(pour_points), "pour points", "ogr")

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
    dialog.comboBox_domainDefinitionType.setCurrentIndex(0)
    dialog.save_input_state()
    dialog.close()

    state_path = Path(input_state_path(tmp_path))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pour_point_outlet_id_field"] == "outlet_name"
    assert "dem_domain" not in state
    assert state["domain_definition_type"] == "Single Domain [DEM extent]"

    restored = MhmQgisDialog()
    restored.project_folder = str(tmp_path)
    restored.load_input_state()

    assert restored.selected_outlet_id_field() == "outlet_name"
    assert restored.selected_outlet_ids() == ["upper", "lower"]
    assert restored.comboBox_domainDefinitionType.currentIndex() == 0
    restored.close()



def test_meteo_variable_and_multiplier_state_round_trip(tmp_path):
    from dataclasses import asdict
    from mhm_qgis.core.meteorology.forcing import MeteoFolderSpec
    from mhm_qgis.qt.controllers import main
    folder = tmp_path / "forcing"
    folder.mkdir()
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog._loading_input_state = True
    dialog.refresh_meteo_folder_sources()
    dialog._add_folder_combo_item(dialog.comboBox_precipitationFile, str(folder))
    dialog.comboBox_precipitationFile.setCurrentIndex(dialog.comboBox_precipitationFile.count() - 1)
    dialog._meteo_inputs = {v: asdict(MeteoFolderSpec(v, str(folder), unit=unit))
                            for v, unit in (("pre", "mm"), ("tavg", "K"))}
    dialog.spinBox_L2ResolutionMultiplier.setValue(4)
    dialog._loading_input_state = False
    dialog.save_input_state()
    dialog.close()
    restored = MhmQgisDialog()
    restored.project_folder = str(tmp_path)
    restored.load_input_state()
    assert restored.spinBox_L2ResolutionMultiplier.value() == 4
    assert restored.tableWidget_meteoInputs.rowCount() == 2
    assert [s.kind for s in restored.selected_meteo_specs()] == ["pre", "tavg"]
    assert restored._meteo_inspections == {}
    restored.close()


def test_meteo_requires_pre_but_not_unconfigured_variables(tmp_path):
    from dataclasses import asdict
    from mhm_qgis.core.meteorology.forcing import MeteoFolderSpec
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    with pytest.raises(ValueError, match="precipitation"):
        dialog.selected_meteo_specs()
    dialog._meteo_inputs = {"pre": asdict(MeteoFolderSpec("pre", str(tmp_path), unit="mm"))}
    assert tuple(s.kind for s in dialog.selected_meteo_specs()) == ("pre",)
    dialog.close()


def test_combined_workflow_green_state_restores_from_workspace(tmp_path):
    _app()
    latlon = Path(data_folder(tmp_path)) / "latlon.nc"
    latlon.parent.mkdir(parents=True)
    latlon.touch()
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    processing.mark_workflow(
        tmp_path,
        "execute_all_meteo",
        "completed",
    )
    dialog.close()

    restored = MhmQgisDialog()
    restored.project_folder = str(tmp_path)
    restored.refresh_morphology_workflow_button_states()

    assert "#2e7d32" in restored.pushButton_executeAllMeteo.styleSheet()
    restored.close()


@pytest.mark.parametrize("workflow,button", [
    ("project_structure", "pushButton_createProjectStructure"),
    ("scan_precipitation", "pushButton_scanPrecipitationFiles"),
    ("execute_all_observations", "pushButton_executeAllObservation"),
    ("run_mhm", "pushButton_execute_mHM"),
])
def test_additional_workflow_green_state_restores(
        tmp_path, workflow, button):
    _app()
    processing.mark_workflow(tmp_path, workflow, "completed")
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog.refresh_morphology_workflow_button_states()

    assert "#2e7d32" in getattr(dialog, button).styleSheet()
    dialog.close()


def test_meteorology_completion_is_independent_of_latlon(tmp_path):
    _app()
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    processing.mark_workflow(
        tmp_path,
        "execute_all_meteo",
        "completed",
    )
    dialog.close()

    restored = MhmQgisDialog()
    restored.project_folder = str(tmp_path)
    restored.refresh_morphology_workflow_button_states()

    assert "#2e7d32" in (
        restored.pushButton_executeAllMeteo.styleSheet()
    )
    assert processing.workflow(tmp_path, "execute_all_meteo")["status"] == "completed"
    restored.close()
