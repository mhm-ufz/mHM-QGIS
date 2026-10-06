"""QGIS runtime dependency bootstrap for the mhm_qgis plugin."""
from __future__ import annotations

import importlib
import os
import re
import shlex
import site
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SKIPPED_QGIS_PACKAGES = {
    "pyqt5",
    "pyqt6",
    "pyside6",
    "qgis",
}

IMPORT_NAME_BY_PACKAGE = {
    "pyyaml": "yaml",
    "netcdf4": "netCDF4",
}

_LAST_RESULT = None


def configure_qtpy_api() -> str:
    """Select QGIS's active binding before QtPy or plotting libraries load."""
    from qgis.PyQt.QtCore import QT_VERSION_STR

    os.environ["QT_API"] = "pyqt6" if QT_VERSION_STR.startswith("6.") else "pyqt5"
    os.environ["PYQTGRAPH_QT_LIB"] = "PyQt6" if QT_VERSION_STR.startswith("6.") else "PyQt5"
    return os.environ["QT_API"]


@dataclass
class Dependency:
    """One install requirement and its import probe name."""

    requirement: str
    package_name: str
    import_name: str


@dataclass
class BootstrapResult:
    """Outcome of a dependency bootstrap check/install pass."""

    ok: bool
    missing: list[Dependency] = field(default_factory=list)
    installed: list[Dependency] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    cancelled: bool = False
    user_site: str = ""

    @property
    def failed_requirements(self) -> list[str]:
        return list(self.failed)


def ensure_qgis_runtime_dependencies(parent=None, prompt=True) -> BootstrapResult:
    """
    Ensure QGIS can import mhm_qgis runtime dependencies.

    Missing packages are installed into the current Python user's site-packages
    with ``pip install --user``. QGIS-owned Qt bindings are deliberately skipped
    even when they appear in the requirements file.
    """

    global _LAST_RESULT  # noqa: PLW0603 -- one cached result per QGIS session

    configure_qtpy_api()
    add_user_site()
    dependencies = read_qgis_requirements()
    missing = [dependency for dependency in dependencies if not can_import(dependency)]
    if not missing:
        result = BootstrapResult(ok=True, user_site=user_site_path())
        _LAST_RESULT = result
        return result

    if not prompt:
        result = BootstrapResult(ok=False, missing=missing, user_site=user_site_path())
        _LAST_RESULT = result
        return result

    if not confirm_install(parent, missing):
        result = BootstrapResult(
            ok=False,
            missing=missing,
            cancelled=True,
            user_site=user_site_path(),
        )
        _LAST_RESULT = result
        return result

    result = install_missing_dependencies(parent, missing)
    _LAST_RESULT = result
    return result


def last_bootstrap_result() -> BootstrapResult | None:
    """Return the most recent bootstrap result."""

    return _LAST_RESULT


def read_qgis_requirements(requirements_path=None) -> list[Dependency]:
    """Read plugin runtime requirements, skipping QGIS-owned packages."""

    path = Path(requirements_path) if requirements_path else default_requirements_path()
    if not path.exists():
        return []

    dependencies = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            dependency = dependency_from_requirement_line(line)
            if dependency is not None:
                dependencies.append(dependency)
    return dependencies


def default_requirements_path() -> Path:
    """Return the plugin-local requirements file, falling back in dev trees."""

    plugin_path = Path(__file__).resolve().parent / "requirements.txt"
    if plugin_path.exists():
        return plugin_path
    return Path(__file__).resolve().parents[1] / "requirements.txt"


def dependency_from_requirement_line(line: str) -> Dependency | None:
    """Parse a simple requirements.txt entry into a dependency probe."""

    requirement = line.split("#", 1)[0].strip()
    if not requirement or requirement.startswith(("-", ".")):
        return None

    package_name = re.split(r"\s*(?:\[|==|!=|~=|>=|<=|>|<|;)", requirement, maxsplit=1)[0]
    package_key = package_name.strip().lower().replace("_", "-")
    if not package_key or package_key in SKIPPED_QGIS_PACKAGES:
        return None

    import_name = IMPORT_NAME_BY_PACKAGE.get(
        package_key,
        package_key.replace("-", "_"),
    )
    return Dependency(
        requirement=requirement,
        package_name=package_name,
        import_name=import_name,
    )


def can_import(dependency: Dependency) -> bool:
    """Return whether a dependency import currently succeeds."""

    try:
        importlib.import_module(dependency.import_name)
        return True
    except ImportError:
        return False


def add_user_site() -> str:
    """Add the current user's site-packages directory to sys.path."""

    path = user_site_path()
    if path:
        try:
            site.addsitedir(path)
        except Exception:
            if path not in sys.path:
                sys.path.append(path)
    importlib.invalidate_caches()
    return path


def user_site_path() -> str:
    """Return the current Python user's site-packages path."""

    return site.USER_SITE or site.getusersitepackages()


def install_missing_dependencies(parent, missing: list[Dependency]) -> BootstrapResult:
    """Install missing dependencies and verify they import afterward."""

    installed = []
    failed = {}

    progress = create_progress_dialog(parent, missing)
    set_wait_cursor(True)
    try:
        break_system_packages = pip_supports_break_system_packages()
        for index, dependency in enumerate(missing, start=1):
            if progress is not None:
                progress.setLabelText(f"Installing {dependency.requirement}...")
                progress.setValue(index - 1)

            command = pip_install_command(dependency.requirement, break_system_packages)
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            add_user_site()
            if completed.returncode == 0 and can_import(dependency):
                installed.append(dependency)
                continue

            output = (completed.stdout or "").strip()
            if completed.returncode == 0:
                output = (
                    f"Installed {dependency.requirement}, but "
                    f"could not import {dependency.import_name}."
                )
            failed[dependency.requirement] = output
    finally:
        set_wait_cursor(False)
        if progress is not None:
            progress.setValue(len(missing))
            progress.close()

    add_user_site()
    remaining = [dependency for dependency in missing if not can_import(dependency)]
    result = BootstrapResult(
        ok=not remaining and not failed,
        missing=remaining,
        installed=installed,
        failed=failed,
        user_site=user_site_path(),
    )

    if result.ok and installed:
        show_info(
            parent,
            "mHM QGIS Dependencies Installed",
            "Installed and loaded mHM QGIS Python dependencies:\n\n"
            + "\n".join(dependency.requirement for dependency in installed),
        )
    elif not result.ok:
        show_failure(parent, result)

    return result


def pip_install_command(requirement: str, break_system_packages=False) -> list[str]:
    """Return the pip install command for one requirement."""

    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--user",
        requirement,
    ]
    if break_system_packages and sys.platform.startswith("linux"):
        command.append("--break-system-packages")
    return command


def pip_supports_break_system_packages() -> bool:
    """Return whether the current pip supports PEP 668 override flag."""

    if not sys.platform.startswith("linux"):
        return False
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception:
        return False
    return "--break-system-packages" in (completed.stdout or "")


def confirm_install(parent, missing: list[Dependency]) -> bool:
    """Ask the user whether missing dependencies may be installed."""

    from qgis.PyQt.QtWidgets import QMessageBox

    message = (
        "mHM QGIS needs these Python packages in the QGIS Python environment:\n\n"
        + "\n".join(dependency.requirement for dependency in missing)
        + "\n\nThey will be installed with pip --user into:\n"
        + user_site_path()
    )
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Install mHM QGIS Python dependencies?")
    box.setText(message)
    install_button = box.addButton("Install", QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    return box.clickedButton() is install_button


def create_progress_dialog(parent, missing: list[Dependency]):
    """Create a small modal progress dialog for dependency installation."""

    try:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QProgressDialog

        progress = QProgressDialog(
            "Installing mHM QGIS Python dependencies...",
            None,
            0,
            len(missing),
            parent,
        )
        progress.setWindowTitle("mHM QGIS Dependency Installation")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        return progress
    except Exception:
        return None


def set_wait_cursor(enabled: bool) -> None:
    """Toggle a wait cursor while pip runs."""

    try:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QApplication

        if enabled:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()
    except Exception:
        pass


def show_info(parent, title: str, message: str) -> None:
    """Show an informational message when Qt is available."""

    try:
        from qgis.PyQt.QtWidgets import QMessageBox

        QMessageBox.information(parent, title, message)
    except Exception:
        print(f"{title}: {message}")  # noqa: T201 -- Qt unavailable


def show_warning(parent, title: str, message: str) -> None:
    """Show a warning message when Qt is available."""

    try:
        from qgis.PyQt.QtWidgets import QMessageBox

        QMessageBox.warning(parent, title, message)
    except Exception:
        print(f"{title}: {message}")  # noqa: T201 -- Qt unavailable


def show_failure(parent, result: BootstrapResult) -> None:
    """Show failed dependency details and manual install commands."""

    failed_names = result.failed_requirements or [
        dependency.requirement for dependency in result.missing
    ]
    break_system_packages = pip_supports_break_system_packages()
    command_lines = [
        " ".join(
            shlex.quote(part)
            for part in pip_install_command(requirement, break_system_packages)
        )
        for requirement in failed_names
    ]
    details = "\n\n".join(
        f"{requirement}:\n{output}"
        for requirement, output in result.failed.items()
        if output
    )
    message = (
        "mHM QGIS could not install or load these Python packages:\n\n"
        + "\n".join(failed_names)
        + "\n\nTry running these commands with the QGIS Python interpreter:\n\n"
        + "\n".join(command_lines)
    )
    if details:
        message += "\n\npip output:\n" + details[-3000:]
    show_warning(parent, "mHM QGIS Dependency Installation Failed", message)
