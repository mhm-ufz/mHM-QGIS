"""Project and domain selection for the main form."""

import json
from pathlib import Path

from qgis.PyQt.QtCore import QSignalBlocker
from qgis.PyQt.QtWidgets import QDialog, QMessageBox

from ...core.handlers.store.layout import ensure_project_structure
from ...qgis_bridge.display import meteo as meteo_display
from ...qgis_bridge.display import output as output_display
from ..dialogs.file_picker import open_directory
from . import grid, input_state, workflow_state
from .version import selected as selected_version
from .workflow_state import set_status, switch_busy


def work_type_changed(dialog):
    single = dialog.comboBox_mhmWorkType.currentIndex() == 0
    dialog.stackedWidget_configuration.setCurrentWidget(
        dialog.page_singleDomain if single else dialog.page_multiDomain
    )
    for page in (
        dialog.tab_geometry,
        dialog.tab_meteo,
        dialog.tab_hydro,
        dialog.tab_execution,
    ):
        page.setEnabled(single)
    dialog.save_input_state()


def create_structure(dialog):
    directory = dialog.project_directory
    if not directory:
        QMessageBox.warning(
            dialog, "Project structure", "Select the outer project folder first."
        )
        return
    if switch_busy(dialog):
        return
    active_domain = False
    try:
        from ...core.handlers.store.paths import domain_folder
        from ...core.handlers.state import settings

        name = dialog.comboBox_domainFolderSelector.currentData()
        if name is None:
            from ..dialogs.domain_folder_name import DomainFolderNameDialog

            prompt = DomainFolderNameDialog(directory, dialog)
            if prompt.exec() != QDialog.DialogCode.Accepted:
                return
            path = prompt.path
            if prompt.source_path:
                from ...core.handlers.store.clone import clone_domain_inputs

                clone_domain_inputs(
                    prompt.source_path, path, selected_version(dialog)
                )
            else:
                path.mkdir(parents=True)
        else:
            path = domain_folder(directory, name)
        from ...core.handlers.store.layout import input_state_path

        state_path = Path(input_state_path(path))
        if state_path.exists() and not isinstance(
            json.loads(state_path.read_text(encoding="utf-8")), dict
        ):
            raise ValueError("The domain input state must be a JSON object.")
        settings.read(path)  # Creates the domain's settings.yaml on first use.
        dialog.save_input_state()
        if dialog._domain_delineator_dialog is not None:
            dialog._domain_delineator_dialog.close()
            dialog._domain_delineator_dialog = None
        dialog.project_folder = str(path)
        active_domain = True
        set_status(dialog, "project_structure", "running")
        dialog.load_input_state()
        ensure_project_structure(path, selected_version(dialog))
        grid.update_l2_resolution_from_metadata(dialog)
        meteo_display.refresh(dialog)
        output_display.refresh(dialog)
        workflow_state.refresh_all(dialog)
        dialog.save_input_state()
        populate_domains(dialog, path.name)
        dialog.comboBox_mHMversion.setEnabled(False)
        set_status(dialog, "project_structure", "completed")
        from ..dialogs.project_folder_tree import ProjectFolderTreeDialog

        ProjectFolderTreeDialog(dialog.project_folder, dialog).exec()
    except (OSError, ValueError) as error:
        if active_domain:
            set_status(dialog, "project_structure", "failed", str(error))
        else:
            workflow_state.set_button_state(dialog, "project_structure", "failed")
        QMessageBox.warning(dialog, "Project structure", str(error))


def populate_domains(dialog, selected=None):
    from ...core.handlers.store.layout import domain_folders

    combo = dialog.comboBox_domainFolderSelector
    with QSignalBlocker(combo):
        combo.clear()
        for path in domain_folders(dialog.project_directory):
            combo.addItem(path.name, path.name)
        combo.addItem("<new>", None)
        index = combo.findData(selected) if selected else 0
        combo.setCurrentIndex(max(0, index))


def select_folder(dialog):
    if switch_busy(dialog):
        return
    directory = open_directory(dialog, "Select Project Folder")
    if not directory:
        return
    dialog.save_input_state()
    if dialog._domain_delineator_dialog is not None:
        dialog._domain_delineator_dialog.close()
        dialog._domain_delineator_dialog = None
    dialog.project_directory = directory
    dialog.project_folder = None
    dialog.comboBox_mHMversion.setEnabled(True)
    for key in workflow_state.specs(dialog):
        workflow_state.set_button_state(dialog, key, "")
    dialog.lineEdit_ProjectFolder.setText(directory)
    dialog._loading_input_state = True
    try:
        input_state.clear(dialog)
    finally:
        dialog._loading_input_state = False
    populate_domains(dialog)
    meteo_display.refresh(dialog)
    output_display.refresh(dialog)
    dialog.log_message(
        "Select a domain and press Create Project Structure to open it."
    )
