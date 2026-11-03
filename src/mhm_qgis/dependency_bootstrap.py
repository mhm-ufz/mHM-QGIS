"""QGIS runtime dependency bootstrap for the mhm_qgis plugin."""
from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import os
import re
import shlex
import site
import subprocess
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path

SKIPPED_QGIS_PACKAGES = {
    "pyqt5",
    "pyqt6",
    "pyside6",
    "qgis",
}

# Checked, never pip-installed: replacing the NumPy QGIS was built with breaks GDAL.
QGIS_BUILT_WITH = {"numpy"}

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

    Missing packages are installed with ``pip install --user`` into the plugin's
    folder in the QGIS profile (``packages_base``), not ``~/.local``. QGIS-owned
    Qt bindings are deliberately skipped even when they appear in the requirements file.
    """

    global _LAST_RESULT  # noqa: PLW0603 -- one cached result per QGIS session

    configure_qtpy_api()
    try:
        from osgeo import gdal_array  # noqa: F401 -- needs the NumPy QGIS ships
    except ImportError as error:
        if error.name != "osgeo":  # no GDAL at all is a plain Python; anything else is a foreign NumPy
            import numpy as np

            folder = Path(np.__file__).parent
            raise RuntimeError(
                f"QGIS's GDAL cannot use NumPy {np.__version__} from {folder} ({error}). "
                "A NumPy installed outside QGIS, e.g. by an earlier `pip install --user`, hides QGIS's own. "
                f"Delete the folders {folder.name} and numpy-{np.__version__}.dist-info in {folder.parent} "
                "and restart QGIS."
            ) from error
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

    parsed = _parsed(requirement)
    if parsed is not None and parsed.marker is not None and not parsed.marker.evaluate():
        return None  # for another Python version
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


def _parsed(requirement: str):
    """`packaging`'s requirement (pip and matplotlib ship it); None without packaging,
    and checks are then import-only."""
    try:
        from packaging.requirements import Requirement
    except ImportError:
        return None
    return Requirement(requirement)


def can_import(dependency: Dependency) -> bool:
    """Return whether a dependency imports at a version its requirement allows.

    An old package already in QGIS's Python must fail here, or it is never
    upgraded (e.g. one written for NumPy < 1.24 then fails on `np.long`)."""

    try:
        importlib.import_module(dependency.import_name)
    except Exception:  # built for another NumPy: "numpy.dtype size changed" is a ValueError
        return False
    parsed = _parsed(dependency.requirement)
    if parsed is None or not parsed.specifier:
        return True
    try:
        return parsed.specifier.contains(importlib.metadata.version(parsed.name), prereleases=True)
    except importlib.metadata.PackageNotFoundError:  # importable without pip metadata: trust it
        return True


def add_user_site() -> str:
    """Put the plugin's package folder first on sys.path, as Python does its user site."""

    path = user_site_path()
    if os.path.isdir(path):
        if path not in sys.path:
            sys.path.insert(0, path)
        site.addsitedir(path)  # its .pth files
    importlib.invalidate_caches()
    return path


def python_executable() -> str:
    """QGIS's own Python. Embedded, sys.executable is the QGIS binary on macOS and Windows,
    and running it with pip's arguments starts another QGIS ("Invalid data source: /-m")."""

    if Path(sys.executable).name.lower().startswith("python"):
        return sys.executable
    version = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    names = ["python.exe"] if os.name == "nt" else [version, "python3", "python"]
    app = Path(sys.executable).parent
    for folder in (Path(sys.exec_prefix), Path(sys.exec_prefix) / "bin", app, app / "bin"):
        for name in names:
            if (folder / name).is_file():
                return str(folder / name)
    raise RuntimeError(f"Could not find the Python of this QGIS under {sys.exec_prefix} or {app}.")


def packages_base() -> str:
    """pip's user base for the plugin: a folder in the QGIS profile, not ~/.local shared by every Python."""

    try:
        from qgis.core import QgsApplication
    except ImportError:  # the standalone runtime: its own environment
        return site.getuserbase()
    import numpy as np

    major = np.__version__.split(".")[0]  # binaries built for NumPy 1 and 2 never share a folder
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "mhm-qgis-packages", f"numpy{major}")


def _user_path(kind: str) -> str:
    return sysconfig.get_path(kind, sysconfig.get_preferred_scheme("user"), vars={"userbase": packages_base()})


def user_site_path() -> str:
    """The plugin's site-packages, where pip --user writes with PYTHONUSERBASE=packages_base()."""

    return _user_path("purelib")


def user_scripts_path() -> str:
    """Where pip puts the installed packages' commands (e.g. `mhm`)."""

    return _user_path("scripts")


def python_environment() -> dict[str, str]:
    """os.environ for a Python the plugin starts (pip, the worker, `mhm`, the terminal).

    QGIS tells its embedded Python where the standard library is. The macOS bundle's
    python3.12, started on its own, is not told: it looks on the build machine and fails
    ("No module named 'encodings'"). It gets QGIS's home, QGIS's import path (its packages,
    so pip reuses them) and the plugin's folder as its user site."""

    environment = {
        **os.environ,
        "PYTHONUSERBASE": packages_base(),
        "PYTHONPATH": os.pathsep.join(item for item in (*sys.path, os.environ.get("PYTHONPATH", "")) if item),
    }
    with contextlib.suppress(RuntimeError):
        if python_executable() != sys.executable and os.path.isdir(sys.prefix):
            environment["PYTHONHOME"] = sys.prefix
    return environment


def pip_environment() -> dict[str, str]:
    """pip --user into the plugin's folder. PEP 668 refuses any install outside a venv on a
    distro Python; with a private user base pip writes only there and never alters a system package."""

    return {**python_environment(), "PIP_BREAK_SYSTEM_PACKAGES": "1"}


def install_missing_dependencies(parent, missing: list[Dependency]) -> BootstrapResult:
    """Install missing dependencies and verify they import afterward."""

    installed = []
    failed = {}
    loaded = {dependency.requirement for dependency in missing if dependency.import_name in sys.modules}

    progress = create_progress_dialog(parent, missing)
    set_wait_cursor(True)
    try:
        environment = pip_environment()
        for index, dependency in enumerate(missing, start=1):
            if progress is not None:
                progress.setLabelText(f"Installing {dependency.requirement}...")
                progress.setValue(index - 1)

            if dependency.package_name.lower() in QGIS_BUILT_WITH:
                failed[dependency.requirement] = (
                    f"QGIS comes with an older {dependency.package_name}; it is not replaced, as QGIS "
                    "was built against it. Use a newer QGIS.")
                continue
            try:
                command = pip_install_command(dependency.requirement)
            except RuntimeError as error:  # no Python found: say so, never start another QGIS
                failed[dependency.requirement] = str(error)
                continue
            completed = subprocess.run(  # nosec B603: this Python's pip, a requirement from the plugin's requirements.txt, no shell
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                env=environment,
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
            + "\n".join(dependency.requirement for dependency in installed)
            + ("\n\nRestart QGIS to load the upgraded ones." if loaded else ""),
        )
    elif not result.ok:
        show_failure(parent, result)

    return result


def pip_install_command(requirement: str) -> list[str]:
    """Return the pip install command for one requirement, holding QGIS's NumPy.

    Without the pin, pip upgrades NumPy for a package whose newest release needs a
    newer one (scipy, pandas, rioxarray need NumPy 2) and GDAL, built against QGIS's,
    fails with `_ARRAY_API not found`. With it, pip picks the newest that still fits."""

    command = [
        python_executable(),
        "-m",
        "pip",
        "install",
        "--user",
        requirement,
    ]
    with contextlib.suppress(importlib.metadata.PackageNotFoundError):
        command.append(f"numpy=={importlib.metadata.version('numpy')}")
    return command


def confirm_install(parent, missing: list[Dependency]) -> bool:
    """Ask the user whether missing dependencies may be installed."""

    from qgis.PyQt.QtWidgets import QMessageBox

    message = (
        "mHM QGIS needs these Python packages in the QGIS Python environment:\n\n"
        + "\n".join(dependency.requirement for dependency in missing)
        + "\n\nThey will be installed into the QGIS profile:\n"
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
    except (ImportError, RuntimeError):  # no QGIS/Qt here
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
    environment = pip_environment()
    names = [name for name in ("PYTHONHOME", "PYTHONUSERBASE") if name in environment]
    prefix = ("".join(f'set "{name}={environment[name]}" && ' for name in names) if os.name == "nt"
              else "".join(f"{name}={shlex.quote(environment[name])} " for name in names)
              + "PIP_BREAK_SYSTEM_PACKAGES=1 ")
    command_lines = []
    with contextlib.suppress(RuntimeError):  # no Python found: the details say so
        command_lines = [
            prefix + " ".join(shlex.quote(part) for part in pip_install_command(requirement))
            for requirement in failed_names
        ]
    details = "\n\n".join(
        f"{requirement}:\n{output}"
        for requirement, output in result.failed.items()
        if output
    )
    message = "mHM QGIS could not install or load these Python packages:\n\n" + "\n".join(failed_names)
    if command_lines:
        message += "\n\nTry running these commands:\n\n" + "\n".join(command_lines)
    if details:
        message += "\n\npip output:\n" + details[-3000:]
    show_warning(parent, "mHM QGIS Dependency Installation Failed", message)
