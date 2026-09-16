"""Independent setup roots and the explicit metadata-dialog boundary."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from mhm_qgis.core.handlers.state import nml_settings, processing
from mhm_qgis.core.handlers.store import layout, paths
from mhm_qgis.core.meteorology.forcing import MeteoFolderSpec
from mhm_qgis.vSpecific import build_initial_values


@pytest.mark.parametrize("version", ["5.13", "6"])
def test_domain_paths_states_and_namelists_are_isolated(tmp_path, version):
    legacy = tmp_path / "mhm-qgis/data/master"
    legacy.mkdir(parents=True)
    (legacy / "keep").write_bytes(b"legacy")
    roots = [paths.domain_folder(tmp_path, name) for name in ("Alpine", "Valley")]
    for number, root in enumerate(roots):
        layout.ensure_project_structure(root, version)
        Path(layout.input_state_path(root)).write_text('{"mhm_version": "' + version + '"}')
        nml_settings.update_section(root, "test", {"number": number})
        nml_settings.sync_domain_settings(root)
        processing.overlay(root, {"test": number})
        assert paths.workspace_folder(root) == str(root)
        assert paths.master_data_folder(root) == str(root / "data")
        assert not (root / "data/master").exists()
        values = build_initial_values(version, SimpleNamespace(project_folder=root))["main"]
        latlon = (values["directories_general"]["file_LatLon"] if version.startswith("5")
                  else values["config_input"]["latlon_path"])
        assert latlon == ["data/latlon.nc"]
        assert "data/master" not in str(values)
        assert nml_settings.load_settings(root)["domains"][0]["data_path"] == "data/"
    assert layout.domain_folders(tmp_path) == tuple(roots)
    assert nml_settings.load_settings(roots[0])["test"] == {"number": 0}
    assert processing.section(roots[1], "test") == 1
    assert (legacy / "keep").read_bytes() == b"legacy"
    assert not (tmp_path / "settings.yaml").exists()


@pytest.mark.parametrize("name", ["", "..", "../outside", "a/b", "a\\b", "data", "Z Temp", "<new>"])
def test_domain_names_cannot_escape_or_replace_legacy_folders(tmp_path, name):
    with pytest.raises(ValueError):
        paths.domain_folder(tmp_path, name)


def test_switching_domains_restores_only_the_selected_setup(tmp_path, monkeypatch):
    pytest.importorskip("qgis.core")
    from mhm_qgis.qt.controllers import main
    from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog
    from mhm_qgis.qt.dialogs.domain_folder_name import DomainFolderNameDialog
    from mhm_qgis.qt.dialogs.project_folder_tree import ProjectFolderTreeDialog
    names = iter(("Alpine", "Valley"))
    def name(prompt):
        prompt.path = paths.domain_folder(tmp_path, next(names))
        return prompt.DialogCode.Accepted
    monkeypatch.setattr(DomainFolderNameDialog, "exec", name)
    monkeypatch.setattr(ProjectFolderTreeDialog, "exec", lambda self: 0)
    dialog = MhmQgisDialog()
    dialog.project_directory = str(tmp_path)
    main.populate_domain_folders(dialog)
    main.create_project_structure(dialog)
    first = Path(dialog.project_folder)
    dialog.spinBox_L2ResolutionMultiplier.setValue(4)
    dialog._meteo_inputs = {"tavg": asdict(MeteoFolderSpec("tavg", str(tmp_path), unit="K"))}
    dialog.save_input_state()
    dialog.comboBox_domainFolderSelector.setCurrentIndex(dialog.comboBox_domainFolderSelector.count() - 1)
    main.create_project_structure(dialog)
    assert Path(dialog.project_folder).name == "Valley"
    assert dialog._meteo_inputs == {}
    assert dialog.spinBox_L2ResolutionMultiplier.value() == 1
    dialog.comboBox_domainFolderSelector.setCurrentIndex(dialog.comboBox_domainFolderSelector.findData("Alpine"))
    main.create_project_structure(dialog)
    assert Path(dialog.project_folder) == first
    assert dialog.spinBox_L2ResolutionMultiplier.value() == 4
    assert set(dialog._meteo_inputs) == {"tavg"}
    assert dialog.tableWidget_meteoInputs.rowCount() == 1
    assert dialog._meteo_inspections == {}
    dialog.close()


def test_meteo_dialog_cancel_and_edit_do_not_prepare_data(tmp_path, monkeypatch):
    pytest.importorskip("qgis.core")
    from mhm_qgis.qt.controllers import main
    from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog
    from mhm_qgis.qt.dialogs.meteo_var_details_input import MeteoVarDetailsDialog
    dialog = MhmQgisDialog()
    dialog.project_folder = str(paths.domain_folder(tmp_path, "Alpine"))
    layout.ensure_project_structure(dialog.project_folder, "6")
    spec = MeteoFolderSpec("tavg", str(tmp_path), unit="K", temporal_resolution="Hourly")
    dialog._meteo_inputs = {"tavg": asdict(spec)}
    def cancel(prompt):
        assert prompt.groupBox.title() == "tavg"
        assert prompt.comboBox_temporalResolutionInput.currentText() == "Hourly"
        assert prompt.comboBox_unitInput.currentText() == "K"
        assert not prompt.comboBox_cumulativeTemporalResolution.isEnabled()
        prompt.checkBox_cumulative.setChecked(True)
        assert prompt.comboBox_cumulativeTemporalResolution.isEnabled()
        return prompt.DialogCode.Rejected
    monkeypatch.setattr(MeteoVarDetailsDialog, "exec", cancel)
    monkeypatch.setattr(dialog.task_coordinator, "submit", lambda *a, **k: pytest.fail("Cancel started scanning"))
    main.populate_meteo_table(dialog)
    dialog.tableWidget_meteoInputs.cellWidget(0, 5).click()
    assert dialog._meteo_inputs["tavg"] == asdict(spec)
    assert not list(Path(dialog.project_folder).rglob("*.nc"))
    dialog.close()
