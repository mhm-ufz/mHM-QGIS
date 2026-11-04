"""Smoke tests for the plain input-combo integration."""

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



# isort: off
pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.core import (QgsVectorLayer)
from qgis.PyQt.QtWidgets import QApplication

from mhm_qgis.qt.dialogs.input_selection import InputComboAdapter
from mhm_qgis.core.handlers.state import processing
from mhm_qgis.core.handlers.store.paths import data_folder
from mhm_qgis.core.handlers.store.layout import input_state_path
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog
from mhm_qgis.qt import project_tree
from mhm_qgis.qt.controllers import main, workflow_state
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


def test_dialog_maps_new_combos_and_lists_no_project_files(tmp_path):
    _app()
    (tmp_path / "dem.tif").touch()

    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog.refresh_input_sources()

    assert isinstance(dialog.input_combo("dem"), InputComboAdapter)
    assert dialog.input_combo("dem").combo_box is dialog.comboBox_demInput
    labels = [dialog.comboBox_demInput.itemText(index) for index in range(dialog.comboBox_demInput.count())]
    assert "dem.tif" not in labels  # only layers loaded in QGIS, and browsed files
    dialog.close()


def test_main_configuration_buttons_use_dialog_first_controller(monkeypatch):
    from mhm_qgis.qt.controllers import model

    _app()
    calls = []
    for name in ("edit_namelists", "run", "version_changed"):
        monkeypatch.setattr(
            model, name, lambda owner, action=name: calls.append((action, owner)),
        )
    dialog = MhmQgisDialog()
    calls.clear()
    dialog.pushButton_edit_nmls.click()
    dialog.pushButton_execute_mHM.click()
    dialog.comboBox_mHMversion.setCurrentIndex(
        (dialog.comboBox_mHMversion.currentIndex() + 1) % dialog.comboBox_mHMversion.count()
    )
    assert calls == [(name, dialog) for name in (
        "edit_namelists", "run", "version_changed",
    )]
    assert not hasattr(dialog, "configuration_processor")
    dialog.close()


def test_the_land_use_eye_shows_the_prepared_periods(tmp_path, monkeypatch):
    from mhm_qgis.core.handlers.state.nml_settings import update_section
    from mhm_qgis.qgis_bridge.display import morphology
    from mhm_qgis.qt import project_tree

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
    rendered = []
    monkeypatch.setattr(
        morphology, "show_raster",
        lambda owner, path, name, **options: rendered.append((path, options)),
    )
    from mhm_qgis.core.handlers.state import processing

    processing.mark_workflow(tmp_path, "execute_all_morphology", "completed")  # A's files count once A has run
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    project_tree.refresh(dialog)
    assert project_tree.item(dialog, "lc").text(project_tree.STATUS_COLUMN) == "✅"
    project_tree.view(dialog, "lc", True)
    assert [(path, options["variable"]) for path, options in rendered] == [(output, "land_cover")]
    assert main.display_morphology(dialog, "dem") == ""  # not prepared
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
    dialog._categorical_ready_configs["soil"] = {"input_path": str(raster)}
    dialog._categorical_modes["soil"] = "Single Categorical Layer (with Lookup Table)"
    main.check(dialog, "soil", dialog._categorical_modes["soil"])
    dialog.comboBox_pourPointOutletID.addItem("old_id")
    dialog.comboBox_pourPointOutletID.setCurrentText("old_id")
    dialog._domain_definition_mode = main.DOMAIN_TYPES[0]
    main.check(dialog, "domainDefinition", main.DOMAIN_TYPES[0])
    dialog._categorical_lookup_configs["soil"] = {
        "lookup_table": str(first_project / "soil.csv"),
        "mapping_field": "source",
        "class_field": "class",
    }

    dialog.project_folder = str(second_project)
    dialog.load_input_state()

    assert dialog._categorical_ready_configs == {}
    assert dialog.categorical_input_mode("soil") == ""
    assert dialog.buttonGroup_soil.checkedButton() is None
    assert dialog.categorical_lookup_config("soil") is None
    assert main.selected_outlet_id_field(dialog) == ""
    assert dialog.buttonGroup_domainDefinition.checkedButton() is None
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
    assert main.selected_outlet_id_field(dialog) == "station_id"
    assert main.selected_outlet_ids(dialog) == ["1", "2"]

    dialog.comboBox_pourPointOutletID.setCurrentText("outlet_name")
    dialog._domain_definition_mode = main.DOMAIN_TYPES[0]
    dialog.save_input_state()
    dialog.close()

    state_path = Path(input_state_path(tmp_path))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pour_point_outlet_id_field"] == "outlet_name"
    assert "dem_domain" not in state
    assert state["domain_definition_type"] == main.DOMAIN_TYPES[0]

    from mhm_qgis.core.handlers.state.domain_state import save_state

    state["pour_point_outlet_id_field"] = ""
    state_path.write_text(json.dumps(state), encoding="utf-8")
    save_state(tmp_path, {"outlet_id_field": "outlet_name"})

    restored = MhmQgisDialog()
    restored.project_folder = str(tmp_path)
    restored.load_input_state()

    assert main.selected_outlet_id_field(restored) == "outlet_name"
    assert main.selected_outlet_ids(restored) == ["upper", "lower"]
    assert restored.buttonGroup_domainDefinition.checkedButton().text() == main.DOMAIN_TYPES[0]
    restored.close()



def test_meteo_variable_and_multiplier_state_round_trip(tmp_path):
    from dataclasses import asdict

    from mhm_qgis.core.meteorology.forcing import MeteoFolderSpec
    from mhm_qgis.qt.controllers import forcing, grid
    folder = tmp_path / "forcing"
    folder.mkdir()
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog._loading_input_state = True
    grid.refresh_precipitation_folder(dialog)
    grid.select_precipitation_folder(dialog, str(folder))
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
    assert [s.kind for s in forcing.selected_meteo_specs(restored)] == ["pre", "tavg"]
    assert restored._meteo_inspections == {}
    restored.close()


def test_meteo_requires_pre_but_not_unconfigured_variables(tmp_path):
    from dataclasses import asdict

    from mhm_qgis.core.meteorology.forcing import MeteoFolderSpec
    from mhm_qgis.qt.controllers import forcing
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    with pytest.raises(ValueError, match="precipitation"):
        forcing.selected_meteo_specs(dialog)
    dialog._meteo_inputs = {"pre": asdict(MeteoFolderSpec("pre", str(tmp_path), unit="mm"))}
    assert tuple(s.kind for s in forcing.selected_meteo_specs(dialog)) == ("pre",)
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
    workflow_state.refresh_all(restored)

    assert "#2e7d32" in project_tree.button(restored, "meteorology").styleSheet()
    restored.close()


@pytest.mark.parametrize(("workflow", "button"), [
    ("project_structure", "pushButton_createOrLoadDomainStructure"),
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
    workflow_state.refresh_all(dialog)

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
    workflow_state.refresh_all(restored)

    assert "#2e7d32" in project_tree.button(restored, "meteorology").styleSheet()
    assert processing.workflow(tmp_path, "execute_all_meteo")["status"] == "completed"
    restored.close()


def test_qgis_removing_layers_keeps_the_saved_grid_and_outlet_field(tmp_path):
    from qgis.core import QgsProject, QgsRasterLayer

    _app()
    dem_path = tmp_path / "dem.asc"
    dem_path.write_text("ncols 2\nnrows 2\nxllcorner 0\nyllcorner 0\ncellsize 1\nNODATA_value -9999\n1 2\n3 4\n")
    dem = QgsRasterLayer(str(dem_path), "dem", "gdal")
    pour_points = tmp_path / "pour_points.geojson"
    pour_points.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"station_id": 1}, "geometry": {"type": "Point", "coordinates": [1, 1]}}]}))
    points = QgsVectorLayer(str(pour_points), "pour points", "ogr")
    QgsProject.instance().addMapLayers([dem, points])
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog.input_combo("dem").setLayer(dem)
    dialog.input_combo("pour_points").setLayer(points)
    assert main.selected_outlet_id_field(dialog) == "station_id"
    processing.overlay(tmp_path, {"grid": {"l2_header": {"cellsize": 2.0}, "configuration_fingerprint": "saved"}})

    QgsProject.instance().removeAllMapLayers()  # closing the QGIS project, quitting or reloading the plugin

    assert processing.grid_metadata(tmp_path)["configuration_fingerprint"] == "saved"
    assert main.selected_outlet_id_field(dialog) == "station_id"
    dialog.close()
