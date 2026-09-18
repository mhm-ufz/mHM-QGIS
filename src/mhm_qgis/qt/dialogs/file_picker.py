"""File dialogs with one remembered directory per parent dialog class."""

from pathlib import Path

from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import QFileDialog


def _key(parent):
    return f"mhm_qgis/last_directory/{type(parent).__name__}"


def _project_directory(parent):
    current = parent
    while current is not None:
        directory = getattr(current, "project_directory", None)
        if directory and Path(directory).is_dir():
            return str(directory)
        current = current.parent() if hasattr(current, "parent") else None
    return ""


def _initial_directory(parent):
    saved = str(QSettings().value(_key(parent), "") or "")
    return saved if saved and Path(saved).is_dir() else _project_directory(parent)


def open_file(parent, title, file_filter):
    path, selected_filter = QFileDialog.getOpenFileName(
        parent, title, _initial_directory(parent), file_filter
    )
    if path:
        QSettings().setValue(_key(parent), str(Path(path).parent))
    return path, selected_filter


def open_directory(parent, title):
    path = QFileDialog.getExistingDirectory(
        parent, title, _initial_directory(parent)
    )
    if path:
        QSettings().setValue(_key(parent), path)
    return path


__all__ = ["open_directory", "open_file"]
