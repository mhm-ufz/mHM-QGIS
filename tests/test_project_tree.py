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
                             ("inputs", dialog.page_demDerivatives, "I. DEM Derivatives"),  # no page: its first child's
                             ("lc", dialog.page_landUse, "III. Land use"),
                             ("domain_definition", dialog.page_domainDefinition, "VII. Domain Definition"),
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


def test_process_cases_list_their_inputs_and_go_to_edit_namelists(dialog, tmp_path):
    import json

    from mhm_qgis.core.handlers.state import nml_settings
    from mhm_qgis.core.handlers.store import layout
    from mhm_qgis.qt.controllers import process_cases

    dialog.comboBox_mHMversion.setCurrentText("6.0")
    (tmp_path / ".temp").mkdir()
    (tmp_path / ".temp" / "mhm_qgis_input_state.json").write_text(json.dumps({"mhm_version": "6.0"}))
    layout.forget_project_version()
    dialog.project_folder = str(tmp_path)
    dialog.treeWidget_projectStructure.setCurrentItem(project_tree.item(dialog, "settings"))
    assert dialog.formLayout_processCases.rowCount() > 5  # one combo per process
    temperatures = [project_tree.item(dialog, f"meteo_{name}") for name in ("tavg", "tmin", "tmax")]
    assert all(row.isHidden() for row in temperatures)  # no case needs them yet
    assert project_tree.item(dialog, "aspect").text(project_tree.STATUS_COLUMN) == "⚪"  # every v6 run needs it
    process_cases.changed(dialog, "pet", 1)  # Hargreaves-Samani: tavg, tmin, tmax and aspect
    assert not any(row.isHidden() for row in temperatures)
    assert nml_settings.load_settings(tmp_path)["intro_values"]["main"]["config_processes"]["pet"] == 1


def test_columns_and_the_add_edit_run_buttons(dialog):
    tree = dialog.treeWidget_projectStructure
    assert (project_tree.STATUS_COLUMN, project_tree.ACTION, project_tree.VIEW) == (1, 2, 3)
    run, add, edit = (project_tree.button(dialog, key) for key in ("morphology", "lc", "slope"))
    assert (run.text(), run.toolTip(), run.isEnabled()) == ("▶", "Run", False)  # nothing added, no setup
    assert (add.text(), add.toolTip()) == ("\N{HEAVY PLUS SIGN}", "Add")
    assert (edit.text(), edit.toolTip(), edit.isEnabled()) == ("✏️", "Edit", False)  # nothing to replace yet
    assert {button.size().width() for button in (run, add, edit, project_tree.button(dialog, "outputs"))} == {24}
    add.click()
    assert tree.currentItem() is project_tree.item(dialog, "lc")
    assert dialog.stackedWidget_project.currentWidget() is dialog.page_landUse


def test_a_type_radio_shows_its_form_and_ok_commits_it(dialog, tmp_path):
    from mhm_qgis.qt.dialogs.single_layer_input import SingleLayerInputDialog

    dialog.project_folder = str(tmp_path)
    dialog.treeWidget_projectStructure.setCurrentItem(project_tree.item(dialog, "lc"))
    radios = dialog.buttonGroup_landUse.buttons()
    assert [radio.text() for radio in radios][1] == "Single Categorical Layer (with Lookup Table)"
    radios[1].click()
    assert isinstance(dialog.verticalLayout_landUseForm.itemAt(0).widget(), SingleLayerInputDialog)
    assert dialog._categorical_modes.get("lc", "") == ""  # not before OK


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
