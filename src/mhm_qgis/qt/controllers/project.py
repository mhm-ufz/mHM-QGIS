"""Project and domain selection for the main form."""

import json
from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem
from qgis.PyQt.QtCore import QSignalBlocker
from qgis.PyQt.QtWidgets import QDialog, QMessageBox

from ...core.handlers.store.layout import ensure_project_structure
from .. import project_tree
from ..dialogs.file_picker import open_directory
from . import (
    input_state,
    intro,
    main,
    multi_domain,
    post_context,
    process_cases,
    workflow_state,
)
from .version import selected as selected_version
from .workflow_state import set_status, switch_busy

POST_ONLY = 3  # comboBox_mhmWorkType: an mHM setup that has run; only its outputs are analysed


def post_only(dialog) -> bool:
    return dialog.comboBox_mhmWorkType.currentIndex() == POST_ONLY


def show_work_type(dialog):
    """Show the page of the selected work type; a combined setup prepares no inputs, and post
    processing only keeps the configuration rows and the outputs."""
    index = dialog.comboBox_mhmWorkType.currentIndex()
    dialog.stackedWidget_configuration.setCurrentWidget(
        {0: dialog.page_singleDomain, POST_ONLY: dialog.page_postProcessingOnly}.get(index, dialog.page_multiDomain)
    )
    # single-domain rows: a combined run takes each domain's periods and their common process cases
    for key in ("resolution", "inputs", "time_period", "settings"):
        project_tree.item(dialog, key).setHidden(index != 0)
    project_tree.item(dialog, "execution").setHidden(index == POST_ONLY)


def work_type_changed(dialog):
    show_work_type(dialog)
    index = dialog.comboBox_mhmWorkType.currentIndex()
    opened = POST_ONLY if dialog.setup_folder == dialog.project_directory else int(dialog._multi_domains is not None)
    if dialog.setup_folder and opened != index:
        # The open setup is of another kind: save and close it.
        dialog.save_input_state()
        dialog.setup_folder = None
        dialog._multi_domains = None
        post_context.clear(dialog)
        dialog.comboBox_mHMversion.setEnabled(True)
        dialog.log_message({0: "Open a domain setup to continue.", POST_ONLY: "Press Set to open the folder "
                            "for post processing."}.get(index, "Open a multi-domain setup to continue."))
    dialog.save_input_state()


def _not_a_run(directory, version) -> str:
    """Why `directory` is not an mHM setup that has run (its namelists and outputs), or ""."""
    from ...core.handlers.store.paths import version_key
    from ...core.post.runs import RunRef, wrote

    folder = Path(directory)
    needed = ("mhm.nml", "parameters.nml" if version.startswith("6") else "mhm_parameter.nml")
    missing = [name for name in needed if not (folder / name).is_file()]
    if missing:
        return f"{folder} holds no {' or '.join(missing)}: post processing needs the setup's mHM {version} namelists."
    if not wrote(RunRef(folder, folder, folder, folder, version_key(version))):
        return f"{folder} holds no mHM output where its namelists put it: run mHM first."
    return ""


def set_post_crs(dialog):
    """The post-processing page's Set: the project folder opens as the setup and its outputs load."""
    crs = dialog.mProjectionSelectionWidget_postCrs.crs()
    if not dialog.project_directory or not crs.isValid():
        QMessageBox.warning(dialog, "Post processing", "Set the project folder first, then choose a valid CRS.")
        return
    if problem := _not_a_run(dialog.project_directory, selected_version(dialog)):
        QMessageBox.warning(dialog, "Post processing only", problem)
        return
    dialog.setup_folder = dialog.project_directory
    dialog._set_crs = crs.authid()
    dialog.comboBox_mHMversion.setEnabled(False)
    dialog.save_input_state()  # its version tells the run listing how to read the namelists
    post_context.populate(dialog, rescan=True)
    project_tree.refresh(dialog)


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
        from ...core.handlers.state import settings
        from ...core.handlers.store.paths import domain_folder

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
        main.embed(dialog, "domainDefinition", None)  # closes an open delineator
        dialog.setup_folder = str(path)
        active_domain = True
        set_status(dialog, "project_structure", "running")
        dialog.load_input_state()
        ensure_project_structure(path, selected_version(dialog))
        post_context.clear(dialog)
        workflow_state.refresh_all(dialog)
        dialog.save_input_state()
        populate_domains(dialog, path.name)
        intro.write_pending(dialog)
        dialog.comboBox_mHMversion.setEnabled(False)
        set_status(dialog, "project_structure", "completed")
        process_cases.inputs(dialog)  # the input rows show once Domain Setup has run
        from ..dialogs.project_folder_tree import ProjectFolderTreeDialog

        ProjectFolderTreeDialog(dialog.setup_folder, dialog).exec()
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
    domain_chosen(dialog)


def domain_chosen(dialog):
    """`<new>` creates a domain; a listed one opens. The version is chosen only with no setup open:
    changing it rewrites the open one."""
    new = dialog.comboBox_domainFolderSelector.currentData() is None
    dialog.pushButton_createOrLoadDomainStructure.setText("New" if new else "Open")
    dialog.comboBox_mHMversion.setEnabled(new and not dialog.setup_folder)


def browse_folder(dialog):
    """Only fill the folder box; Set opens it."""
    directory = open_directory(dialog, "Select Project Folder")
    if directory:
        dialog.lineEdit_ProjectFolder.setText(directory)


def set_folder(dialog):
    """Open the project folder in the box."""
    directory = dialog.lineEdit_ProjectFolder.text().strip()
    if not directory or not Path(directory).is_dir():
        QMessageBox.warning(dialog, "Project folder", "Browse to an existing project folder first.")
        return
    if post_only(dialog) and (problem := _not_a_run(directory, selected_version(dialog))):
        QMessageBox.warning(dialog, "Post processing only", problem)
        return
    if switch_busy(dialog):
        return
    dialog.save_input_state()
    main.embed(dialog, "domainDefinition", None)
    dialog.project_directory = directory
    dialog.setup_folder = None
    dialog.comboBox_mHMversion.setEnabled(True)
    for key in workflow_state.specs(dialog):
        workflow_state.set_button_state(dialog, key, "")
    dialog._loading_input_state = True
    try:
        input_state.clear(dialog)
    finally:
        dialog._loading_input_state = False
    populate_domains(dialog)
    multi_domain.populate_setups(dialog)
    post_context.clear(dialog)
    if post_only(dialog):
        from ...core.handlers.file import json as jsonio
        from ...core.handlers.store.layout import input_state_path

        saved = jsonio.read(input_state_path(directory), {}) or {}
        dialog.mProjectionSelectionWidget_postCrs.setCrs(QgsCoordinateReferenceSystem(saved.get("crs_authid") or ""))
        dialog.log_message("Set the CRS of the setup's grids on Domain Setup to open its outputs.")
    else:
        dialog.log_message("Select a domain and press Create Project Structure to open it.")
    project_tree.refresh(dialog)
