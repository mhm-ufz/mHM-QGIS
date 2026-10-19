"""Combining prepared domains: checks, combined namelists for v5.13 and v6, the page."""
import json
from pathlib import Path

import pytest

pytest.importorskip("nml_tools.gui.model")

from nml_tools.gui.model import load_project, render_profile

from mhm_qgis.core import multi_domain
from mhm_qgis.core.handlers.store import layout
from mhm_qgis.core.handlers.store.paths import schemas_folder

V5_MAIN = """&mainconfig
  nDomains = 1
  resolution_Hydrology(1) = 1000.0
  L0Domain(1) = 1
/
&directories_general
  dirConfigOut = "output/"
  dirCommonFiles = "data/static/morph/"
  dir_Morpho(1) = "data/static/morph/"
  dir_Out(1) = "output/"
  file_LatLon(1) = "data/latlon.nc"
/
&processSelection
  processCase = 1, 1, 1, 1, 0, 1, 1, 3, 1, 0, 0
/
&LCover
  nLCoverScene = 1
  LCoverYearStart(1) = {start}
  LCoverYearEnd(1) = 2020
  LCoverfName(1) = "lc.asc"
/
&evaluation_gauges
  nGaugesTotal = {count}
  NoGauges_domain(1) = {count}
  Gauge_id(1,:) = {ids}
/
"""
V6_MAIN = "&config_project\n  n_domains = 1\n  n_geo_units = {geo}\n/\n&config_processes\n  pet = {pet}\n/\n"


def _domain(project, name, version, text):
    path = project / "mhm-qgis" / name
    (path / ".temp").mkdir(parents=True)
    (path / ".temp" / "mhm_qgis_input_state.json").write_text(json.dumps({"mhm_version": version}))
    (path / "mhm.nml").write_text(text)
    layout.forget_project_version()
    return path


def _v5(project, name, ids="1, 2", start=1990):
    count = len(ids.split(","))
    return _domain(project, name, "5.13", V5_MAIN.format(ids=ids, count=count, start=start))


def test_add_domain_checks(tmp_path):
    root = tmp_path / "mhm-qgis-multi" / "M"
    root.mkdir(parents=True)
    a = _v5(tmp_path, "A")
    v6 = _domain(tmp_path, "B", "6", V6_MAIN.format(geo=10, pet=1))
    assert multi_domain.validate_domain(a, "5.13", root) is None
    assert "uses mHM v6" in multi_domain.validate_domain(v6, "5.13", root)
    assert "already in the list" in multi_domain.validate_domain(a, "5.13", root, [a])
    assert "not a domain setup" in multi_domain.validate_domain(tmp_path, "5.13", root)
    assert "itself" in multi_domain.validate_domain(root, "5.13", root)
    (a / "mhm.nml").unlink()
    assert "no saved mhm.nml" in multi_domain.validate_domain(a, "5.13", root)


def test_v513_blocks_different_land_cover_and_combines_the_arrays(tmp_path):
    root = tmp_path / "mhm-qgis-multi" / "M"
    root.mkdir(parents=True)
    a, b = _v5(tmp_path, "A"), _v5(tmp_path, "B", ids="7", start=2000)
    errors, _warnings = multi_domain.validate_set([a, b], "5.13", root)
    assert any(error.startswith("Different land-cover scenes") for error in errors)
    assert multi_domain.validate_set([a], "5.13", root)[0] == ["Combine at least two domains."]

    (b / "mhm.nml").write_text(V5_MAIN.format(ids="7", count=1, start=1990))
    assert multi_domain.validate_set([a, b], "5.13", root) == ([], [])
    values, dimensions = multi_domain.v5_values([a, b], root)
    main = values["main"]
    assert main["mainconfig"]["nDomains"] == 2
    assert main["mainconfig"]["L0Domain"] == [1, 2]
    assert main["directories_general"]["dir_Morpho"] == [
        "../../mhm-qgis/A/data/static/morph/", "../../mhm-qgis/B/data/static/morph/"]
    assert main["directories_general"]["dir_Out"] == ["output/A/", "output/B/"]  # never into a domain
    assert main["directories_general"]["dirConfigOut"] == "output/"
    assert main["LCover"]["LCoverfName"] == ["lc.asc"]  # a name inside dir_LCover(i)
    assert main["evaluation_gauges"]["Gauge_id"] == [[1, 2], [7, 0]]
    assert main["evaluation_gauges"]["nGaugesTotal"] == 3
    assert dimensions["nGaugesTotal"] == 2  # the widest row sizes the editor's arrays
    project = load_project(schemas_folder("5.13"), root, None)
    profile = next(p for p in project.profiles if p.name == "main")
    text = "".join(render_profile(project, profile, main, {k.lower(): v for k, v in dimensions.items()}).values())
    assert "Gauge_id(2,1) = 7" in text


def test_v6_combine_nml_lists_the_domains(tmp_path):
    root = tmp_path / "mhm-qgis-multi" / "M"
    root.mkdir(parents=True)
    a = _domain(tmp_path, "A", "6", V6_MAIN.format(geo=8, pet=1))
    b = _domain(tmp_path, "B", "6", V6_MAIN.format(geo=10, pet=1))
    errors, warnings = multi_domain.validate_set([a, b], "6", root)
    assert errors == []
    assert any("replacing that domain's single-run outputs" in w for w in warnings)
    values, dimensions = multi_domain.v6_values([a, b], root)
    meta = values["meta"]
    assert meta["config_domain"] == {"domain_dirs": ["../../mhm-qgis/A", "../../mhm-qgis/B"],
                                     "domain_nmls": ["mhm.nml", "mhm.nml"]}
    assert meta["config_project"]["n_domains"] == 2
    assert meta["config_project"]["read_domains_from_dirs"] is True
    assert meta["config_project"]["n_geo_units"] == 10  # each domain's must be <= this
    assert meta["config_processes"] == {"pet": 1}
    assert dimensions == {"n_domains": 2, "n_geo_units": 10}
    (b / "mhm.nml").write_text(V6_MAIN.format(geo=10, pet=2))
    assert any(e.startswith("Different process cases") for e in multi_domain.validate_set([a, b], "6", root)[0])


def test_prepare_seeds_shared_namelists_and_output_folders(tmp_path):
    root = tmp_path / "mhm-qgis-multi" / "M"
    a, b = _v5(tmp_path, "A"), _v5(tmp_path, "B", ids="7")
    (a / "mhm_parameter.nml").write_text("&interception1\n/\n")
    (a / "mhm_outputs.nml").write_text("&NLoutputResults\n/\n")
    (b / "mrm_outputs.nml").write_text("&NLoutputResults_mrm\n/\n")
    assert multi_domain.prepare([a, b], "5.13", root) == [
        "mhm_parameter.nml", "mhm_outputs.nml", "mrm_outputs.nml"]
    assert (root / "mrm_outputs.nml").read_text() == "&NLoutputResults_mrm\n/\n"
    assert (root / "output" / "B").is_dir()
    (b / "mrm_outputs.nml").write_text("changed")
    assert multi_domain.prepare([a, b], "5.13", root) == []  # never overwrites the multi copy
    assert (root / "mrm_outputs.nml").read_text() == "&NLoutputResults_mrm\n/\n"


def test_multi_page_creates_lists_prepares_and_runs(tmp_path, monkeypatch):
    pytest.importorskip("qgis.core")
    from qgis.PyQt.QtCore import QCoreApplication, QEvent
    from qgis.PyQt.QtWidgets import QInputDialog, QMessageBox

    from mhm_qgis.core.handlers.state import nml_settings
    from mhm_qgis.qt.controllers import model
    from mhm_qgis.qt.controllers import multi_domain as page
    from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog
    from mhm_qgis.vSpecific import build_initial_values

    a = _domain(tmp_path, "A", "6", V6_MAIN.format(geo=10, pet=1))
    b = _domain(tmp_path, "B", "6", V6_MAIN.format(geo=10, pet=1))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("Combined", True))
    dialog = MhmQgisDialog()
    dialog.comboBox_mHMversion.setCurrentText("6.0")
    dialog.project_directory = str(tmp_path)
    dialog.comboBox_mhmWorkType.setCurrentIndex(1)
    page.populate_setups(dialog)
    assert page.create_or_load(dialog)
    assert not dialog.comboBox_mHMversion.isEnabled()
    for domain in (a, b, b):
        dialog.lineEdit_domainFolder.setText(str(domain))
        page.add_domain(dialog)
    assert warnings[-1].endswith("is already in the list.")
    assert dialog.tableWidget_domainInputs.rowCount() == 2
    dialog.tableWidget_domainInputs.cellWidget(1, 1).click()  # remove B
    assert dialog._multi_domains == ["../../mhm-qgis/A"]
    assert not page.prepare(dialog)
    assert "Combine at least two domains." in warnings[-1]
    dialog.lineEdit_domainFolder.setText(str(b))
    page.add_domain(dialog)
    assert page.prepare(dialog)
    root = Path(dialog.project_folder)
    saved = json.loads((root / ".temp" / "mhm_qgis_input_state.json").read_text())
    assert saved["setup"] == "multi"
    assert saved["domains"] == ["../../mhm-qgis/A", "../../mhm-qgis/B"]
    assert nml_settings.load_settings(root)["multi_domain"]["domains"] == saved["domains"]
    assert build_initial_values("6.0", dialog)["meta"]["config_domain"]["domain_dirs"] == saved["domains"]

    commands = []
    monkeypatch.setattr(model, "require_v6_executable", lambda owner: "/opt/mhm-driver")
    monkeypatch.setattr(model, "inputs_ready", lambda owner, folder: True)
    monkeypatch.setattr("mhm_qgis.externals.mhm_handler.executable",
                        lambda version, v6_path=None: Path(v6_path))
    class Terminal:
        def run_command(self, command, **_options):
            commands.append(command)
            return True

    monkeypatch.setattr(dialog, "open_project_terminal", Terminal)
    assert model.run(dialog)
    assert commands == ["/opt/mhm-driver -n combine.nml"]

    dialog.comboBox_mhmWorkType.setCurrentIndex(0)  # back to single: the combined setup closes
    assert dialog.project_folder is None
    assert dialog._multi_domains is None
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
