"""The project tree: Designer rows with tree.yml keys, pages in the Project tab, statuses."""
import pytest

pytest.importorskip("qgis.core", reason="QGIS runtime libraries are required")

from qgis.PyQt.QtCore import QCoreApplication, QDateTime, QEvent, Qt
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
    for key, page, title in (("domain_setup", dialog.page_setup, "Domain Setup"),
                             ("resolution", dialog.page_resolution, "Resolution"),
                             ("inputs", dialog.page_inputs, "2. Inputs"),
                             ("morphology", dialog.page_morphology, "A. Morphology"),
                             ("dem", dialog.page_dem, "DEM"),
                             ("lc", dialog.page_landUse, "Land use"),
                             ("domain_definition", dialog.page_domainDefinition, "Domain Definition"),
                             ("meteorology", dialog.page_meteo, "B. Meteorology"),
                             ("meteo_pre", dialog.page_precipitation, "Precipitation"),
                             ("meteo_tavg", dialog.page_meteoVar, "Mean air temperature"),
                             ("observations", dialog.page_hydro, "C. Observations"),
                             ("outputs", dialog.page_outputs, "4. Outputs")):
        tree.setCurrentItem(project_tree.item(dialog, key))
        assert dialog.stackedWidget_project.currentWidget() is page
        assert dialog.tabWidget_steps.tabText(0) == title


def test_project_setup_status_follows_the_folder(dialog, tmp_path):
    row = project_tree.item(dialog, "project_configuration")
    assert row.text(project_tree.STATUS_COLUMN) == "⚪"
    assert row.toolTip(project_tree.STATUS_COLUMN) == "Empty: no inputs added"
    dialog.lineEdit_ProjectFolder.setText(str(tmp_path))
    assert row.text(project_tree.STATUS_COLUMN) == "🟢"  # browsed, not yet Set
    dialog.pushButton_setProject.click()
    assert row.text(project_tree.STATUS_COLUMN) == "✅"


def test_each_set_button_is_followed_by_its_rows_status(dialog, tmp_path):
    dialog.lineEdit_ProjectFolder.setText(str(tmp_path))
    dialog.pushButton_setProject.click()
    nodes = [node for node in project_tree.load().values() if "set" in node]
    assert len(nodes) == 10
    for node in nodes:
        row, label = project_tree.item(dialog, node["key"]), getattr(dialog, node["set"])
        assert (node["key"], label) in dialog._indicators
        assert label.text() == row.text(project_tree.STATUS_COLUMN)
        assert label.toolTip() == row.toolTip(project_tree.STATUS_COLUMN)
    assert dialog.label_setProjectStatus.text() == "✅"


def test_the_stack_pages_follow_the_tree_order(dialog):
    pages = [dialog.stackedWidget_project.widget(i).objectName() for i in range(dialog.stackedWidget_project.count())]
    used = list(dict.fromkeys(project_tree._node(row)["page"] for row in project_tree._rows(dialog)
                              if "page" in project_tree._node(row)))
    assert [page for page in pages if page in used] == used


def test_the_meteo_form_shows_its_variables_status_right_of_set(dialog):
    from mhm_qgis.qt.controllers import forcing, main

    forcing.show_meteo_input(dialog, "pre")
    form, row = main.form_on(dialog, "precipitation"), project_tree.item(dialog, "meteo_pre")
    assert dialog.widget_hostPrecipitationMeteoVarInput.isAncestorOf(form)  # its own page, with the period
    for label in (form.label_setStatus, dialog.label_setPrecipitationInputStatus):
        assert ("meteo_pre", label) in dialog._indicators
        assert (label.text(), label.toolTip()) == (row.text(project_tree.STATUS_COLUMN),
                                                   row.toolTip(project_tree.STATUS_COLUMN))
    assert form.label_description.text().startswith("Set the resolutions")  # no grid, no scan yet
    forcing.show_meteo_input(dialog, "tavg")
    assert ("meteo_tavg", main.form_on(dialog, "meteoVar").label_setStatus) in dialog._indicators
    project_tree.refresh(dialog)


def test_precipitation_is_complete_once_its_period_is_set(dialog, tmp_path):
    def status():
        project_tree.refresh(dialog)
        return project_tree.item(dialog, "meteo_pre").text(project_tree.STATUS_COLUMN)

    _set_up(dialog, tmp_path)
    assert status() == "⚪"
    dialog._meteo_inputs["pre"] = {"kind": "pre", "folder": str(tmp_path)}
    assert status() == "🟢"  # its Set scanned, the period is not Set yet
    dialog.pushButton_setSimulationPeriod.click()
    assert status() == "✅"
    assert dialog.label_setPrecipitationInputStatus.text() == "✅"


def test_set_crs_without_an_open_setup_asks_to_create_or_open_one(dialog, monkeypatch):
    from qgis.core import QgsCoordinateReferenceSystem

    from mhm_qgis.qt.controllers import grid

    warnings = []
    monkeypatch.setattr(grid.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    before = dialog._set_crs
    dialog.mProjectionSelectionWidget_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:3035"))
    dialog.pushButton_setCRS.click()
    assert warnings == ["Create a new domain setup or open an existing one (Open) before setting the CRS."]
    assert dialog._set_crs == before


def test_a_finished_meteo_run_rechecks_the_inputs_without_a_click(dialog, tmp_path, monkeypatch):
    from mhm_qgis.qt.controllers import process_cases, workflow_state

    checks = []
    monkeypatch.setattr(process_cases, "inputs", checks.append)
    dialog.setup_folder = str(tmp_path)
    dialog._running_workflow = "execute_all_meteo"
    workflow_state.finish(dialog, "execute_all_meteo", True, "")
    assert checks == [dialog]


def test_a_coloured_text_workflow_button_keeps_its_native_size(dialog):
    from qgis.PyQt.QtWidgets import QPushButton

    from mhm_qgis.qt.controllers import workflow_state

    button = dialog.pushButton_createOrLoadDomainStructure
    native = QPushButton(button.text()).sizeHint()
    workflow_state.set_button_state(dialog, "project_structure", "completed")
    button.adjustSize()
    assert button.width() >= native.width()
    assert button.height() >= native.height()
    assert "#a5d6a7" in button.styleSheet()


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
    assert dialog.stackedWidget_project.currentWidget() is dialog.page_outputs
    assert dialog._post.chosen == "signatures"


def test_domain_setup_pages_reference_buttons_and_combined_domains(dialog):
    tree = dialog.treeWidget_projectStructure
    for key, page in (("resolution", dialog.page_resolution), ("settings", dialog.page_settings)):
        tree.setCurrentItem(project_tree.item(dialog, key))
        assert dialog.stackedWidget_project.currentWidget() is page
    assert dialog.scrollArea_resolution.widget().isAncestorOf(dialog.lineEdit_L0Resolution)  # scrolls both ways
    assert dialog.page_settings.isAncestorOf(dialog.pushButton_edit_nmls)
    tree.setCurrentItem(project_tree.item(dialog, "dem"))
    dialog.pushButton_refL0Derivatives.click()
    assert tree.currentItem() is project_tree.item(dialog, "resolution")
    dialog.comboBox_mhmWorkType.setCurrentIndex(1)  # combine existing domains
    assert [project_tree.item(dialog, key).isHidden() for key in ("resolution", "settings")] == [True, False]


def _set_up(dialog, tmp_path):
    """A v6 project whose Domain Setup has run."""
    import json

    from mhm_qgis.core.handlers.state import processing
    from mhm_qgis.core.handlers.store import layout

    dialog.comboBox_mHMversion.setCurrentText("6.0")
    (tmp_path / ".temp").mkdir()
    (tmp_path / ".temp" / "mhm_qgis_input_state.json").write_text(json.dumps({"mhm_version": "6.0"}))
    layout.forget_project_version()
    dialog.setup_folder = str(tmp_path)
    processing.mark_workflow(tmp_path, "project_structure", "completed")


def test_input_rows_wait_for_domain_setup(dialog, tmp_path):
    from mhm_qgis.qt.controllers import process_cases

    groups = [project_tree.item(dialog, key) for key in ("morphology", "meteorology", "observations")]
    rows = [group.child(i) for group in groups for i in range(group.childCount())]
    assert not any(group.isHidden() for group in groups)
    assert all(row.isHidden() for row in rows)
    dialog.setup_folder = str(tmp_path)
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
    assert tree.currentItem() is project_tree.item(dialog, "resolution")


def test_domain_setup_and_dem_derivative_rows_show_status_and_eye(dialog, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from mhm_qgis.qgis_bridge.display import grid as grid_display
    from mhm_qgis.qt.controllers import grid, process_cases

    _set_up(dialog, tmp_path)
    process_cases.inputs(dialog)
    keys = ("resolution", "settings", "dem")

    def statuses():
        project_tree.refresh(dialog)
        return [project_tree.item(dialog, key).text(project_tree.STATUS_COLUMN) for key in keys]

    def eye(key):
        return not project_tree.item(dialog, key).icon(project_tree.VIEW).isNull()

    assert statuses() == ["⚪"] * 3
    assert not any(eye(key) for key in keys[:2])
    dialog._grid_l0_info = {"resolution": 100.0, "unit": "m"}
    assert statuses()[0] == "🟢"  # an L0 file, no Set grid yet
    dialog._grid_l2_metadata = {"l1_header": {"ncols": 1}, "configuration_fingerprint": "set"}
    monkeypatch.setattr(grid, "grid_fingerprint", lambda _dialog: "set")
    (tmp_path / "mhm.nml").write_text("")
    monkeypatch.setattr(dialog.input_combo("dem"), "currentLayer", object)
    assert statuses() == ["✅", "✅", "🟢"]  # the DEM Set fits the grid, ▶ fills it and derives
    assert all(eye(key) for key in keys[:2])
    dialog._dem_error = "The DEM does not cover the common extent."
    assert statuses()[2] == "⚪"
    dialog._input_findings["latlon"] = SimpleNamespace(ok=False)  # v5.13 also needs latlon.nc
    assert statuses()[0] == "🟢"
    del dialog._input_findings["latlon"]
    monkeypatch.setattr(grid, "grid_fingerprint", lambda _dialog: "changed")  # e.g. another L2 file
    assert statuses()[0] == "🟢"  # the Set grid no longer counts

    monkeypatch.setattr(grid, "grid_fingerprint", lambda _dialog: "set")
    shown, opened = [], []
    monkeypatch.setattr(grid_display, "display_grids", lambda _dialog, levels: shown.append(levels) or ["L0", "L2"])
    monkeypatch.setattr(dialog.pushButton_editNmlsFinal, "click", lambda: opened.append(True))
    resolution = project_tree.item(dialog, "resolution")
    statuses()
    project_tree.clicked(dialog, resolution, project_tree.VIEW)
    assert shown == [["L0", "L1", "L11", "L2"]]  # one eye, every grid
    assert dialog._viewed["resolution"] == ["L0", "L2"]
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
    buttons = [dialog.toolButton_landUseCategoricalInfo,
               *(getattr(dialog, f"toolButton_{page}LookupInfo") for page in ("soil", "geology", "lai"))]
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
    dialog.project_directory = str(tmp_path)  # as Set does
    assert pointed() == ["resolution"]
    dialog._grid_l0_info = {"resolution": 100.0, "unit": "m"}
    dialog._grid_l2_metadata = {"l1_header": {"ncols": 1}, "configuration_fingerprint": "set"}
    monkeypatch.setattr(grid, "grid_fingerprint", lambda _dialog: "set")
    assert pointed() == ["settings"]
    monkeypatch.setattr(grid, "grid_fingerprint", lambda _dialog: "changed")
    assert pointed() == ["resolution"]  # an input changed: Set the grid again


def test_type_radios_are_designer_widgets_with_the_saved_mode_texts(dialog):
    from mhm_qgis.qt.controllers import categorical, main

    for kind, page in categorical.PAGES.items():
        assert [radio.text() for radio in getattr(dialog, f"buttonGroup_{page}").buttons()] == list(categorical.MODES[kind])
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


def test_next_step_waits_on_domain_setup_then_on_a_set_grid(dialog, tmp_path, monkeypatch):
    from mhm_qgis.qt.controllers import grid

    def status(key):
        project_tree.refresh(dialog)
        return project_tree.item(dialog, key).text(project_tree.STATUS_COLUMN)

    dialog.lineEdit_ProjectFolder.setText(str(tmp_path))
    dialog.project_directory = str(tmp_path)  # as Set does
    assert status("domain_setup") == "⚪"
    assert project_tree.item(dialog, "domain_setup").text(project_tree.NEXT) == "➤"
    _set_up(dialog, tmp_path)
    assert status("domain_setup") == "✅"
    assert status("resolution") == "⚪"
    dialog._grid_l0_info = {"resolution": 100.0, "unit": "m"}
    assert status("resolution") == "🟢"
    monkeypatch.setattr(grid, "grid_current", lambda _dialog: True)
    assert status("resolution") == "✅"


def test_unsaved_form_input_survives_a_tree_switch_and_a_reload(dialog, tmp_path):
    from mhm_qgis.qt.controllers import main

    _set_up(dialog, tmp_path)
    tree = dialog.treeWidget_projectStructure
    tree.setCurrentItem(project_tree.item(dialog, "meteo_tavg"))
    main.form_on(dialog, "meteoVar").lineEdit_fileNameGlobDefinition.setText("tavg_*.nc")
    tree.setCurrentItem(project_tree.item(dialog, "meteo_tmin"))
    tree.setCurrentItem(project_tree.item(dialog, "meteo_tavg"))
    assert main.form_on(dialog, "meteoVar").lineEdit_fileNameGlobDefinition.text() == "tavg_*.nc"
    dialog._drafts = {}
    dialog.load_input_state()
    assert dialog._drafts["meteo:tavg"]["pattern"] == "tavg_*.nc"
    assert "meteo:tmin" not in dialog._drafts  # untouched forms leave no draft


def test_forcing_must_cover_the_simulation_period_shown_on_the_time_bar(dialog, tmp_path):
    from types import SimpleNamespace

    import numpy as np

    from mhm_qgis.qt.controllers import forcing, post_context, time

    _set_up(dialog, tmp_path)
    dialog.dateTimeEdit_simulationStartTimeInput.setDateTime(QDateTime.fromString("2000-01-01T00:00", Qt.DateFormat.ISODate))
    dialog.dateTimeEdit_simulationEndTimeInput.setDateTime(QDateTime.fromString("2000-01-10T00:00", Qt.DateFormat.ISODate))
    steps = np.array(["2000-01-01"], dtype="datetime64[s]")
    post_context.time_axis(dialog, np.column_stack([steps, steps]), print)  # standalone: the bar exists once used
    time.save(dialog)
    assert dialog._post.ui.horizontalSlider_postTime.maximum() == 9  # ten days
    short = SimpleNamespace(start_time="2000-01-01T00:00", end_time="2000-01-05T00:00")
    assert "not the simulation period 2000-01-01 to 2000-01-10" in forcing._outside_period(dialog, "tavg", short)
    long = SimpleNamespace(start_time="1999-12-31T00:00", end_time="2000-01-10T00:00")
    assert forcing._outside_period(dialog, "tavg", long) is None


def test_inputs_count_after_their_run_until_a_new_crs_and_a_failed_stage_gets_the_pointer(dialog, tmp_path, monkeypatch):
    from qgis.core import QgsCoordinateReferenceSystem

    from mhm_qgis.core.handlers.state import processing
    from mhm_qgis.core.handlers.store import morphology as store
    from mhm_qgis.qt.controllers import process_cases

    _set_up(dialog, tmp_path)
    process_cases.inputs(dialog)
    monkeypatch.setattr(store, "resolve_display_output", lambda *_args: "prepared.nc")  # every output exists
    dem = project_tree.item(dialog, "dem")

    def status():
        project_tree.refresh(dialog)
        return dem.text(project_tree.STATUS_COLUMN)

    gdal = pytest.importorskip("osgeo.gdal")
    from mhm_qgis.qt.controllers import main

    dem_path = str(tmp_path / "dem.tif")
    gdal.GetDriverByName("GTiff").Create(dem_path, 2, 2, 1).FlushCache()
    main.add_file_input(dialog, "dem", dem_path, manual=True, source_crs="EPSG:32632")
    dialog._dem_error = None  # as a DEM Set on the common grid leaves it
    assert status() == "🟢"  # A has not run: its files do not count
    processing.mark_workflow(tmp_path, "execute_all_morphology", "completed")
    assert status() == "✅"
    dialog.mProjectionSelectionWidget_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:3035"))
    assert status() == "✅"  # nothing applies before Set
    dialog.pushButton_setCRS.click()
    assert processing.workflow(tmp_path, "execute_all_morphology")["status"] == "pending"
    assert status() == "⚪"  # the grid must be Set again in the new CRS before the DEM fits it
    assert dialog.label_demDescription.text().startswith("Set the resolutions first")
    dialog._failed_step = "dem"
    project_tree.refresh(dialog)
    assert dem.text(project_tree.NEXT) == "➤"


def test_set_gauges_builds_the_network_the_delineator_opens_on(dialog, tmp_path, monkeypatch):
    from mhm_qgis.qt.controllers import main
    from mhm_qgis.qt.workflows import hydrology

    started, notes = [], []
    monkeypatch.setattr(main, "_selected_outlets", lambda *_args: ["1"])
    monkeypatch.setattr(hydrology, "start_domain_preflight", lambda *args: started.append(args) or True)
    monkeypatch.setattr(main.QMessageBox, "information", lambda *args: notes.append(args[2]))
    dialog.setup_folder = str(tmp_path)
    assert main.open_domain_delineator(dialog) is False  # Set the Gauges first
    assert not started
    assert "Set on the Gauges page" in notes[-1]
    dialog.pushButton_setGauges.click()
    assert started
    assert len(notes) == 1  # no popup over the running task: a failure's message must show alone


def test_an_embedded_form_drops_its_window_minimum(dialog):
    """The delineator's .ui minimum (800 x 560) is a window's: embedded, it would push the page past
    the dialog's right edge, so only the form's layout minimum counts."""
    from mhm_qgis.qt.controllers import main

    form = QDialog()
    form.setMinimumSize(800, 560)
    main.embed(dialog, "domainDefinition", form)
    assert (form.minimumSize().width(), form.minimumSize().height()) == (0, 0)
    assert dialog.widget_domainDefinitionForm.minimumSizeHint().width() < 800
    main.embed(dialog, "domainDefinition", None)
