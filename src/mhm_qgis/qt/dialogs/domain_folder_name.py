"""Collect a new setup directory name using the Designer form."""
from qgis.PyQt.QtWidgets import QDialog, QMessageBox
from ...core.handlers.store.paths import domain_folder
from ...core.handlers.store.layout import domain_folders
from ..ui import load_form


class DomainFolderNameDialog(QDialog, load_form("domain_folder_name.ui")):
    def __init__(self, project_directory, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.setWindowTitle("New domain folder")
        self.project_directory = project_directory
        self.path = None
        self.source_path = None
        self.comboBox_existingDomainFolderSelector.clear()
        for path in domain_folders(project_directory):
            self.comboBox_existingDomainFolderSelector.addItem(path.name, str(path))
        self.groupBox_loadInputSettingsExistingDomain.setEnabled(
            self.comboBox_existingDomainFolderSelector.count() > 0)

    def accept(self):
        """Accept a safe unused directory name; never overwrite an existing setup."""
        try:
            path = domain_folder(self.project_directory, self.lineEdit.text())
            if path.exists():
                raise ValueError("That folder already exists. Select it from the domain list.")
        except ValueError as error:
            QMessageBox.warning(self, "Domain name", str(error))
            return
        self.path = path
        if self.groupBox_loadInputSettingsExistingDomain.isChecked():
            self.source_path = self.comboBox_existingDomainFolderSelector.currentData()
        super().accept()
