"""Multi-domain setups: create or open one, list its domains, prepare the combined run.

The setup's domains live in `dialog._multi_domains` (stored paths, see
`core.multi_domain.store_paths`); None means no combined setup is open.
"""
from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QSignalBlocker
from qgis.PyQt.QtWidgets import QInputDialog, QMessageBox, QTableWidgetItem, QToolButton

from ...core import multi_domain
from ...core.handlers.state import nml_settings
from ...core.handlers.store.layout import ensure_multi_structure
from ..dialogs.file_picker import open_directory
from .version import selected as selected_version
from .workflow_state import switch_busy


def populate_setups(dialog, selected=None):
    combo = dialog.comboBox_multiDomainFolderSelector
    with QSignalBlocker(combo):
        combo.clear()
        if dialog.project_directory:
            for path in multi_domain.multi_folders(dialog.project_directory):
                combo.addItem(path.name, path.name)
        combo.addItem("<new>", None)
        combo.setCurrentIndex(max(0, combo.findData(selected) if selected else 0))


def create_or_load(dialog):
    directory = dialog.project_directory
    if not directory:
        QMessageBox.warning(dialog, "Multi-domain setup", "Select the outer project folder first.")
        return False
    if switch_busy(dialog):
        return False
    name = dialog.comboBox_multiDomainFolderSelector.currentData()
    try:
        if name is None:
            text, ok = QInputDialog.getText(dialog, "New multi-domain setup", "Setup name:")
            if not ok:
                return False
            root = multi_domain.multi_folder(directory, text)
            if root.exists():
                raise ValueError("That folder already exists. Select it from the list.")
            ensure_multi_structure(root)
        else:
            root = multi_domain.multi_folder(directory, name)
        dialog.save_input_state()  # the setup being left
        dialog.project_folder = str(root)
        if name is None:
            dialog._multi_domains = []
            dialog.save_input_state()
        dialog.load_input_state()
        if dialog._multi_domains is None:
            dialog._multi_domains = []
        populate_setups(dialog, root.name)
        refresh_table(dialog)
        dialog.comboBox_mHMversion.setEnabled(False)
        dialog.log_message(f"Multi-domain setup opened: {root}")
        return True
    except (OSError, ValueError) as error:
        QMessageBox.warning(dialog, "Multi-domain setup", str(error))
        return False


def browse_domain(dialog):
    path = open_directory(dialog, "Select a prepared domain setup")
    if path:
        dialog.lineEdit_domainFolder.setText(path)


def add_domain(dialog):
    if dialog._multi_domains is None:
        QMessageBox.warning(dialog, "Add domain", "Create or open a multi-domain setup first.")
        return False
    path = dialog.lineEdit_domainFolder.text().strip()
    root = dialog.project_folder
    existing = multi_domain.resolve(root, dialog._multi_domains)
    reason = multi_domain.validate_domain(path, selected_version(dialog), root, existing) if path \
        else "Choose a domain folder first."
    if reason:
        QMessageBox.warning(dialog, "Add domain", reason)
        return False
    dialog._multi_domains += multi_domain.store_paths(root, [path])
    dialog.lineEdit_domainFolder.clear()
    refresh_table(dialog)
    dialog.save_input_state()
    return True


def remove_domain(dialog, stored):
    dialog._multi_domains = [item for item in dialog._multi_domains if item != stored]
    refresh_table(dialog)
    dialog.save_input_state()


def refresh_table(dialog):
    table = dialog.tableWidget_domainInputs
    domains = dialog._multi_domains or []
    table.setRowCount(len(domains))
    for row, stored in enumerate(domains):
        item = QTableWidgetItem(stored)
        item.setToolTip(str(multi_domain.resolve(dialog.project_folder, [stored])[0]))
        table.setItem(row, 0, item)
        button = QToolButton(text="✕")
        button.setStyleSheet("color: #c0392b; font-weight: bold;")
        button.setToolTip("Remove this domain")
        # Per-row remove button → remove_domain (wired where the row is built)
        button.clicked.connect(lambda _checked=False, stored=stored: remove_domain(dialog, stored))
        table.setCellWidget(row, 1, button)


def prepare(dialog):
    """Check the domains agree, then write the multi_domain handoff for the editor."""
    if dialog._multi_domains is None:
        QMessageBox.warning(dialog, "Prepare", "Create or open a multi-domain setup first.")
        return False
    root, version = dialog.project_folder, selected_version(dialog)
    paths = multi_domain.resolve(root, dialog._multi_domains)
    errors, warnings = multi_domain.validate_set(paths, version, root)
    for warning in warnings:
        dialog.log_message(f"WARNING: {warning}")
    if errors:
        QMessageBox.warning(dialog, "Prepare the combined run",
                            "The domains cannot be combined yet:\n\n• " + "\n• ".join(errors))
        return False
    copied = multi_domain.prepare(paths, version, root)
    nml_settings.update_section(root, multi_domain.SECTION, {"domains": list(dialog._multi_domains)})
    names = ", ".join(Path(path).name for path in paths)
    dialog.log_message(f"Combined run prepared for {names}. Open Edit Namelists to review it.")
    if copied:
        dialog.log_message(f"Copied {', '.join(copied)} from {paths[0].name}; edit them here from now on.")
    return True
