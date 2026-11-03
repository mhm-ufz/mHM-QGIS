"""The project tree: Designer rows with tree.yml keys, pages in the Project tab, statuses."""
import pytest

pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt
from qgis.PyQt.QtWidgets import QDialog

from mhm_qgis.qt import project_tree
from mhm_qgis.qt.dialogs.intro_wizard import IntroWizard
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog


@pytest.fixture
def dialog():
    made = MhmQgisDialog()
    yield made
    made.close()
    made.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_rows_match_tree_yml_and_open_on_project_configuration(dialog):
    tree = dialog.treeWidget_projectStructure
    rows = list(project_tree._rows(dialog))
    assert sorted(row.text(0) for row in rows) == sorted(project_tree.load())  # Designer and tree.yml agree
    assert tree.columnCount() == len(project_tree.COLUMNS)
    assert [tree.headerItem().text(c) for c in (1, 2, 3)] == ["", "", ""]
    assert [tree.columnWidth(c) for c in (1, 2, 3)] == [28, 28, 28]  # one symbol each
    assert all(row.data(0, Qt.ItemDataRole.UserRole) for row in rows)
    assert tree.currentItem() is project_tree.item(dialog, "project_configuration")
    assert dialog.tabWidget_steps.count() == 1  # the step tabs are pages of the tree now
    assert dialog.tabWidget_steps.tabText(0) == "1. Project Configuration"
    assert dialog.stackedWidget_project.currentWidget() is dialog.page_projectSetup
    assert dialog.page_projectSetup.isAncestorOf(dialog.lineEdit_ProjectFolder)
    assert not hasattr(dialog, "pushButton_launchIntroductionWizard")  # replaced by the Introduction row


def test_each_row_shows_its_page_titled_after_it(dialog):
    tree = dialog.treeWidget_projectStructure
    for key, page, title in (("domain_setup", dialog.tab, "Domain Setup"),
                             ("inputs", dialog.page_inputs, "2. Inputs"),
                             ("morphology", dialog.page_morphology, "A. Morphology"),
                             ("dem", dialog.page_demDerivatives, "DEM"),
                             ("lc", dialog.page_landUse, "Land use"),
                             ("domain_definition", dialog.page_domainDefinition, "Domain Definition"),
                             ("meteorology", dialog.tab_meteo, "B. Meteorology"),
                             ("meteo_pre", dialog.page_meteoVar, "Precipitation"),
                             ("observations", dialog.tab_hydro, "C. Observations"),
                             ("outputs", dialog.tab_outputs, "4. Outputs")):
        tree.setCurrentItem(project_tree.item(dialog, key))
        assert dialog.stackedWidget_project.currentWidget() is page
        assert dialog.tabWidget_steps.tabText(0) == title


def test_project_setup_status_follows_the_folder(dialog, tmp_path):
    row = project_tree.item(dialog, "project_configuration")
    assert row.text(project_tree.STATUS_COLUMN) == "⚪"
    assert row.toolTip(project_tree.STATUS_COLUMN) == "Empty: no inputs added"
    dialog.lineEdit_ProjectFolder.setText(str(tmp_path))
    assert row.text(project_tree.STATUS_COLUMN) == "✅"


def test_introduction_embeds_the_wizard_and_finish_applies_it(dialog):
    dialog.treeWidget_projectStructure.setCurrentItem(project_tree.item(dialog, "intro"))
    assert dialog.stackedWidget_project.currentWidget() is dialog.page_projectIntro
    wizard = dialog.page_projectIntro.findChild(IntroWizard)
    assert not wizard.isWindow()
    assert project_tree.item(dialog, "intro").text(project_tree.STATUS_COLUMN) == "⚪"
    wizard.done(QDialog.DialogCode.Accepted)
    assert dialog._intro_answers == wizard.answers()
    assert project_tree.item(dialog, "intro").text(project_tree.STATUS_COLUMN) == "✅"
    assert not wizard.isHidden()  # ready for another pass


def test_the_eye_shows_whether_a_row_is_on_the_map(dialog):
    project_tree.set_view(dialog, "morphology", False)
    row = project_tree.item(dialog, "morphology")
    assert not row.icon(project_tree.VIEW).isNull()
    assert row.toolTip(project_tree.VIEW) == "Hidden"
    project_tree.set_view(dialog, "morphology", True)
    assert row.toolTip(project_tree.VIEW) == "Shown on the map"


def test_the_splitter_takes_the_height_not_the_externals_bar(dialog):
    dialog.resize(1300, 900)
    dialog.show()
    QCoreApplication.processEvents()
    bar = dialog.groupBox_externals
    assert bar.height() <= bar.sizeHint().height() + 4


def test_output_rows_wait_for_a_run_and_groups_select_their_first_view(dialog):
    groups = [project_tree.item(dialog, key) for key in ("basics", "diagnostics", "advanced")]
    assert all(row.isHidden() for row in groups)  # no run open
    assert project_tree.button(dialog, "outputs").text() == "↺"
    assert project_tree.item(dialog, "outputs").toolTip(project_tree.VIEW) == "Hidden"
    project_tree.outputs_shown(dialog, True)
    dialog.treeWidget_projectStructure.setCurrentItem(project_tree.item(dialog, "diagnostics"))
    assert dialog.treeWidget_projectStructure.currentItem() is project_tree.item(dialog, "signatures")
    assert dialog.stackedWidget_project.currentWidget() is dialog.tab_outputs
    assert dialog._post.chosen == "signatures"


def test_domain_setup_pages_reference_buttons_and_combined_domains(dialog):
    tree = dialog.treeWidget_projectStructure
    for key, page in (("l0_reference", dialog.page_l0Reference), ("l2_reference", dialog.page_l2Reference),
                      ("resolution", dialog.page_resolution), ("settings", dialog.page_settings)):
        tree.setCurrentItem(project_tree.item(dialog, key))
        assert dialog.stackedWidget_project.currentWidget() is page
    assert dialog.page_l0Reference.isAncestorOf(dialog.label_L0Resolution)
    assert dialog.page_settings.isAncestorOf(dialog.pushButton_edit_nmls)
    tree.setCurrentItem(project_tree.item(dialog, "resolution"))
    dialog.pushButton_refL2.click()
    assert tree.currentItem() is project_tree.item(dialog, "l2_reference")
    dialog.comboBox_mhmWorkType.setCurrentIndex(1)  # combine existing domains
    assert [project_tree.item(dialog, key).isHidden() for key in ("l0_reference", "l2_reference", "resolution", "settings")] \
        == [True, True, True, False]


def test_resolution_summary_is_l0_times_the_multiplier(dialog):
    dialog._grid_l0_info = {"resolution": 100.0}
    dialog.label_L0ResolutionUnit.setText("m")
    dialog.spinBox_L2ResolutionMultiplier.setValue(5)
    dialog.treeWidget_projectStructure.setCurrentItem(project_tree.item(dialog, "resolution"))
    assert (dialog.label_resL0Value.text(), dialog.label_resL2Value.text(), dialog.label_resL2Unit.text()) == ("100", "500", "m")
    assert dialog.spinBox_resL2Multiplier.value() == 5
    assert dialog.spinBox_resL2Multiplier.isReadOnly()


def _set_up(dialog, tmp_path):
    """A v6 project whose Domain Setup has run."""
    import json

    from mhm_qgis.core.handlers.state import processing
    from mhm_qgis.core.handlers.store import layout

    dialog.comboBox_mHMversion.setCurrentText("6.0")
    (tmp_path / ".temp").mkdir()
    (tmp_path / ".temp" / "mhm_qgis_input_state.json").write_text(json.dumps({"mhm_version": "6.0"}))
    layout.forget_project_version()
    dialog.project_folder = str(tmp_path)
    processing.mark_workflow(tmp_path, "project_structure", "completed")


def test_input_rows_wait_for_domain_setup(dialog, tmp_path):
    from mhm_qgis.qt.controllers import process_cases

    groups = [project_tree.item(dialog, key) for key in ("morphology", "meteorology", "observations")]
    rows = [group.child(i) for group in groups for i in range(group.childCount())]
    assert not any(group.isHidden() for group in groups)
    assert all(row.isHidden() for row in rows)
    dialog.project_folder = str(tmp_path)
    process_cases.inputs(dialog)
    assert all(row.isHidden() for row in rows)  # a project, but no Domain Setup yet
    (tmp_path / ".temp").mkdir()
    from mhm_qgis.core.handlers.state import processing

    processing.mark_workflow(tmp_path, "project_structure", "completed")
    process_cases.inputs(dialog)
    assert not project_tree.item(dialog, "meteo_pre").isHidden()
    gauges = [project_tree.item(dialog, key) for key in ("gauges", "domain_definition")]
    assert all(row.isHidden() for row in gauges)  # gauges are optional
    dialog.checkBox_gaugeOption.setChecked(True)
    assert not any(row.isHidden() for row in gauges)


def test_process_cases_are_kept_in_the_state_and_the_namelists(dialog, tmp_path):
    import json

    from mhm_qgis.core import datamodel
    from mhm_qgis.core.handlers.state import nml_settings
    from mhm_qgis.core.handlers.store.paths import namelist_project, schemas_folder
    from mhm_qgis.externals.nml_tools_handler import write_namelists
    from mhm_qgis.qt.controllers import process_cases

    _set_up(dialog, tmp_path)
    dialog.treeWidget_projectStructure.setCurrentItem(project_tree.item(dialog, "settings"))
    combos = [dialog.formLayout_processCases.itemAt(i).widget() for i in range(dialog.formLayout_processCases.count())]
    assert all("namelist value" not in combo.itemText(0) for combo in combos if hasattr(combo, "itemText"))
    temperatures = [project_tree.item(dialog, f"meteo_{name}") for name in ("tmin", "tmax")]
    process_cases.changed(dialog, {"pet": -1})  # read from file: no extremes (snow's default reads tavg)
    assert all(row.isHidden() for row in temperatures)
    process_cases.changed(dialog, {"pet": 1})  # Hargreaves-Samani: tavg, tmin, tmax and aspect
    assert not any(row.isHidden() for row in temperatures)
    dialog.treeWidget_projectStructure.setCurrentItem(project_tree.item(dialog, "dem"))
    assert not dialog.tableWidget_demDerivatives.isRowHidden(2)  # aspect, now needed
    state = json.loads((tmp_path / ".temp" / "mhm_qgis_input_state.json").read_text())
    assert state["process_cases"]["pet"] == 1
    assert nml_settings.load_settings(tmp_path)["intro_values"]["main"]["config_processes"]["pet"] == 1  # no namelist yet
    write_namelists(tmp_path, schemas_folder("6.0"), {"main": {"config_processes": {"pet": 1}}},
                    project=namelist_project("6.0", False))
    process_cases.changed(dialog, {"pet": 2})
    assert datamodel.current_processes(tmp_path)["pet"] == 2  # written to the saved namelist at once


def test_summary_tables_on_the_inputs_morphology_and_dem_derivative_pages(dialog, tmp_path):
    from mhm_qgis.qt.controllers import process_cases

    _set_up(dialog, tmp_path)
    process_cases.inputs(dialog)
    tree = dialog.treeWidget_projectStructure
    tree.setCurrentItem(project_tree.item(dialog, "inputs"))
    table = dialog.tableWidget_inputsSummary
    names = [table.item(row, 0).text() for row in range(table.rowCount())]
    assert {"DEM", "Precipitation"} <= set(names)
    assert table.item(names.index("Precipitation"), 1).text().startswith("⚪")
    tree.setCurrentItem(project_tree.item(dialog, "morphology"))
    table = dialog.tableWidget_morphologySummary
    assert "Precipitation" not in [table.item(row, 0).text() for row in range(table.rowCount())]
    tree.setCurrentItem(project_tree.item(dialog, "dem"))
    table = dialog.tableWidget_demDerivatives
    needed = [not table.isRowHidden(row) for row in range(table.rowCount())]
    assert needed == [True] + [name in dialog._input_findings for name in ("slope", "aspect", "fdir", "facc")]
    assert not dialog.widget_demOverride.isVisibleTo(dialog)
    table.cellClicked.emit(1, 0)
    assert dialog._dem_override == "slope"
    assert dialog.widget_demOverride.isVisibleTo(dialog)
    dialog.pushButton_refL0Derivatives.click()
    assert tree.currentItem() is project_tree.item(dialog, "l0_reference")


def test_domain_setup_and_dem_derivative_rows_show_status_and_eye(dialog, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from mhm_qgis.qgis_bridge.display import grid as grid_display
    from mhm_qgis.qt.controllers import grid, process_cases

    _set_up(dialog, tmp_path)
    process_cases.inputs(dialog)
    keys = ("l0_reference", "l2_reference", "resolution", "settings", "dem")

    def statuses():
        project_tree.refresh(dialog)
        return [project_tree.item(dialog, key).text(project_tree.STATUS_COLUMN) for key in keys]

    def eye(key):
        return not project_tree.item(dialog, key).icon(project_tree.VIEW).isNull()

    assert statuses() == ["⚪"] * 5
    assert not any(eye(key) for key in keys[:4])
    dialog._grid_l0_info = {"resolution": 100.0, "unit": "m"}
    dialog._grid_l2_header = {"cellsize": 1000.0}
    dialog._grid_l2_metadata = {"configuration_fingerprint": "scanned"}
    monkeypatch.setattr(grid, "grid_fingerprint", lambda _dialog: "scanned")
    dialog.lineEdit_L1Resolution.setText("1000")
    dialog.lineEdit_L11Resolution.setText("1000")
    (tmp_path / "mhm.nml").write_text("")
    assert statuses() == ["✅", "✅", "✅", "✅", "🟢"]  # the DEM is read, ▶ fills it and derives
    assert all(eye(key) for key in keys[:4])
    dialog._input_findings["latlon"] = SimpleNamespace(ok=False)  # v5.13 also needs latlon.nc
    assert statuses()[2] == "🟢"
    del dialog._input_findings["latlon"]
    monkeypatch.setattr(grid, "grid_fingerprint", lambda _dialog: "changed")  # e.g. new precipitation files
    assert statuses()[1] == "⚪"  # the saved grid no longer counts; no folder to scan

    shown, opened = [], []
    monkeypatch.setattr(grid_display, "display_grids", lambda _dialog, levels: shown.append(levels) or ["L1", "L11/L3"])
    monkeypatch.setattr(dialog.pushButton_editNmlsFinal, "click", lambda: opened.append(True))
    resolution = project_tree.item(dialog, "resolution")
    project_tree.clicked(dialog, resolution, project_tree.VIEW)
    assert shown == [["L1", "L11"]]  # one eye, both grids
    assert dialog._viewed["resolution"] == ["L1", "L11/L3"]
    project_tree.clicked(dialog, resolution, project_tree.VIEW)
    assert "resolution" not in dialog._viewed
    project_tree.clicked(dialog, project_tree.item(dialog, "settings"), project_tree.VIEW)
    assert opened == [True]


def test_the_delineator_type_is_kept_only_once_it_opens(dialog, monkeypatch):
    from mhm_qgis.qt.controllers import main

    monkeypatch.setattr(main.QMessageBox, "warning", lambda *_args: None)  # "Select a project folder first."
    dialog.radioButton_domainDefinitionDelineator.click()
    assert dialog._domain_definition_mode == ""
    assert not dialog.radioButton_domainDefinitionDelineator.isChecked()
    dialog.radioButton_domainDefinitionDemExtent.setChecked(True)  # switched while it was prepared
    main._show_domain_delineator(dialog, {}, None, "", [])
    assert dialog.verticalLayout_domainDefinitionForm.count() == 0


def test_the_domain_row_shows_the_checked_types_form_once(dialog, monkeypatch):
    from qgis.PyQt.QtWidgets import QWidget

    from mhm_qgis.qt.controllers import main

    opened = []
    monkeypatch.setattr(main, "open_domain_delineator", lambda _dialog: opened.append("delineator"))
    monkeypatch.setattr(main, "open_domain_assignment", lambda _dialog: opened.append("assignment"))
    tree, row = dialog.treeWidget_projectStructure, project_tree.item(dialog, "domain_definition")
    main.check(dialog, "domainDefinition", main.DOMAIN_TYPES[1])  # as a loaded setup restores it
    tree.setCurrentItem(row)
    main.check(dialog, "domainDefinition", main.DOMAIN_TYPES[0])
    tree.setCurrentItem(project_tree.item(dialog, "lc"))
    tree.setCurrentItem(row)
    assert opened == ["delineator", "assignment"]
    main.embed(dialog, "domainDefinition", QWidget())  # a form already shown is kept
    tree.setCurrentItem(project_tree.item(dialog, "lc"))
    tree.setCurrentItem(row)
    assert opened == ["delineator", "assignment"]


def test_a_hidden_current_row_selects_its_shown_parent(dialog, tmp_path):
    from mhm_qgis.qt.controllers import process_cases

    _set_up(dialog, tmp_path)
    process_cases.changed(dialog, {"pet": 1})  # Hargreaves-Samani reads tmin
    tree = dialog.treeWidget_projectStructure
    tree.setCurrentItem(project_tree.item(dialog, "meteo_tmin"))
    process_cases.changed(dialog, {"pet": -1})
    assert tree.currentItem() is project_tree.item(dialog, "meteorology")


def test_cases_chosen_for_one_version_do_not_carry_over(dialog):
    dialog.comboBox_mHMversion.setCurrentText("6.0")
    dialog._process_cases = {"pet": 1}
    dialog.comboBox_mHMversion.setCurrentText("5.13")
    assert dialog._process_cases == {}


def test_info_buttons_explain_the_lookup_and_discharge_tables(dialog, monkeypatch):
    from qgis.PyQt.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda _parent, title, text: shown.append((title, text)))
    buttons = [getattr(dialog, f"toolButton_{page}LookupInfo") for page in ("landUse", "soil", "geology", "lai")]
    buttons.append(dialog.toolButton_domainDefinitionInfo)
    for button in buttons:
        assert button.text() == "i"
        assert "Example" in button.toolTip()
        assert "<" not in button.toolTip()  # plain text: only the button is styled
        button.click()
    assert shown == [(button.accessibleName(), button.toolTip()) for button in buttons]


def test_the_parameter_namelist_widgets_follow_their_checkbox(dialog):
    widgets = (dialog.label_parameterArgument, dialog.lineEdit_pathParameterArgument,
               dialog.pushButton_browseParameterNamelistFile)
    assert not any(widget.isEnabled() for widget in widgets)
    dialog.checkBox_enableParameterArgument.setChecked(True)
    assert all(widget.isEnabled() for widget in widgets)


def test_columns_the_run_buttons_and_the_next_step(dialog, tmp_path, monkeypatch):
    from mhm_qgis.qt.controllers import grid

    tree = dialog.treeWidget_projectStructure
    assert (project_tree.STATUS_COLUMN, project_tree.VIEW, project_tree.NEXT) == (1, 2, 3)
    assert tree.header().visualIndex(project_tree.NEXT) == 0  # left of the rows
    run = project_tree.button(dialog, "morphology")
    assert (run.text(), run.toolTip(), run.isEnabled()) == ("▶", "Run", False)  # nothing added, no setup
    assert project_tree.button(dialog, "lc") is None  # no add or edit buttons
    assert {button.size().width() for button in (run, project_tree.button(dialog, "outputs"))} == {24}

    def pointed():
        project_tree.refresh(dialog)
        return [row.data(0, Qt.ItemDataRole.UserRole) for row in project_tree._rows(dialog)
                if row.text(project_tree.NEXT) == "➤"]

    assert pointed() == ["project_configuration"]  # no project folder yet
    assert project_tree.item(dialog, "intro").foreground(project_tree.NEXT).color().name() == "#0000ff"
    _set_up(dialog, tmp_path)
    dialog.lineEdit_ProjectFolder.setText(str(tmp_path))
    assert pointed() == ["l0_reference"]
    dialog._grid_l0_info = {"resolution": 100.0, "unit": "m"}
    dialog._grid_l2_header = {"cellsize": 1000.0}
    dialog._grid_l2_metadata = {"configuration_fingerprint": "scanned"}
    monkeypatch.setattr(grid, "precipitation_folder", lambda _dialog: str(tmp_path))
    monkeypatch.setattr(grid, "grid_fingerprint", lambda _dialog: "changed")
    assert pointed() == ["l2_reference"]  # scan the precipitation again


def test_a_type_radio_shows_its_form_and_ok_commits_it(dialog, tmp_path):
    from mhm_qgis.qt.dialogs.single_layer_input import SingleLayerInputDialog

    dialog.project_folder = str(tmp_path)
    dialog.treeWidget_projectStructure.setCurrentItem(project_tree.item(dialog, "lc"))
    radios = dialog.buttonGroup_landUse.buttons()
    assert [radio.text() for radio in radios][1] == "Single Categorical Layer (with Lookup Table)"
    radios[1].click()
    form = dialog.verticalLayout_landUseForm.itemAt(0).widget()
    assert isinstance(form, SingleLayerInputDialog)
    assert form.pushButton_set.text() == "Set"
    assert dialog._categorical_modes.get("lc", "") == ""  # not before Set


def test_type_radios_are_designer_widgets_with_the_saved_mode_texts(dialog):
    from mhm_qgis.qt.controllers import categorical, main

    for kind, page in categorical.PAGES.items():
        assert [radio.text() for radio in getattr(dialog, f"buttonGroup_{page}").buttons()] == list(categorical.MODES[kind])
        assert getattr(dialog, f"verticalLayout_{page}Types").spacing() == 0
    assert [radio.text() for radio in dialog.buttonGroup_domainDefinition.buttons()] == list(main.DOMAIN_TYPES)
    assert dialog.radioButton_domainDefinitionDelineator.text() == main.DOMAIN_TYPES[1]


def test_embedding_a_form_closes_the_old_one_without_its_slots(dialog):
    from mhm_qgis.qt.controllers import main

    closed, rejected = [], []

    class Old(QDialog):
        def closeEvent(self, event):
            closed.append(True)
            super().closeEvent(event)

    old = Old()
    old.rejected.connect(lambda: rejected.append(True))
    main.embed(dialog, "domainDefinition", old)
    assert dialog.widget_domainDefinitionForm.isAncestorOf(old)
    main.embed(dialog, "domainDefinition", QDialog())
    assert closed == [True]
    assert rejected == []


def test_a_forcing_row_embeds_its_details_and_steps_on_the_time_toolbar(dialog):
    import numpy as np

    from mhm_qgis.qt.controllers import post_context
    from mhm_qgis.qt.dialogs.meteo_var_details_input import MeteoVarDetailsDialog

    dialog.treeWidget_projectStructure.setCurrentItem(project_tree.item(dialog, "meteo_tavg"))
    assert isinstance(dialog.verticalLayout_meteoVarForm.itemAt(0).widget(), MeteoVarDetailsDialog)
    steps = np.array(["2000-01-01", "2000-01-02", "2000-01-03"], dtype="datetime64[s]")
    post_context.time_axis(dialog, np.column_stack([steps, steps]), print)  # standalone: made on first use
    assert dialog._post.ui.horizontalSlider_postTime.maximum() == 2
    assert dialog._post.axis[1] is print
