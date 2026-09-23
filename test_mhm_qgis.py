# E:\0 Python\mhm_qgis\mhm_qgis\test\test_dialog_standalone.py

import os
import sys
from pathlib import Path


if sys.platform == "win32":
    DEFAULT_QGIS_ROOT = Path(r"C:\Program Files\QGIS 3.40.6")
else:
    DEFAULT_QGIS_ROOT = Path("/usr")

REPO_ROOT = Path(__file__).resolve().parent
SRC_ROOT = REPO_ROOT / "src"
PLUGIN_PACKAGE_DIR = SRC_ROOT / "mhm_qgis"


def _existing_paths(paths):
    return [str(path) for path in paths if Path(path).exists()]


def _normalized(path):
    return os.path.normcase(os.path.abspath(path))


def _set_env_paths(name, paths):
    os.environ[name] = os.pathsep.join(_existing_paths(paths))


def _prepend_sys_path(paths):
    new_paths = _existing_paths(paths)
    new_path_keys = {_normalized(path) for path in new_paths}
    sys.path[:] = [
        path for path in sys.path if not path or _normalized(path) not in new_path_keys
    ]
    sys.path[:0] = new_paths


def _add_dll_directories(paths):
    if not hasattr(os, "add_dll_directory"):
        return

    for path in _existing_paths(paths):
        try:
            os.add_dll_directory(path)
        except OSError:
            pass


def _set_env_path(name, path):
    if Path(path).exists():
        os.environ[name] = str(path)


def configure_qgis_environment():
    """Prepare a standalone PyQGIS runtime before importing qgis modules."""
    if sys.platform != "win32":
        # On Linux/macOS, typical system installations don't need the extensive
        # environment setup required by OSGeo4W on Windows.
        if "QGIS_PREFIX_PATH" not in os.environ:
            os.environ["QGIS_PREFIX_PATH"] = str(DEFAULT_QGIS_ROOT)
        
        # src/ holds the plugin package; the repo root stays for test helpers
        # And we might need the QGIS python plugins path for 'processing'
        python_paths = [
            SRC_ROOT,
            REPO_ROOT,
            DEFAULT_QGIS_ROOT / "share" / "qgis" / "python",
            DEFAULT_QGIS_ROOT / "share" / "qgis" / "python" / "plugins"
        ]
        _prepend_sys_path(python_paths)
        _set_env_paths("PYTHONPATH", python_paths)
        return

    qgis_root = Path(os.environ.get("QGIS_ROOT", DEFAULT_QGIS_ROOT))
    qgis_prefix = Path(
        os.environ.get("QGIS_PREFIX_PATH", qgis_root / "apps" / "qgis-ltr")
    )

    if not qgis_prefix.exists():
        for candidate in (qgis_root / "apps" / "qgis-ltr", qgis_root / "apps" / "qgis"):
            if candidate.exists():
                qgis_prefix = candidate
                break

    if not qgis_prefix.exists():
        raise RuntimeError(
            "Could not find QGIS. Set QGIS_ROOT or QGIS_PREFIX_PATH to your QGIS install."
        )

    qgis_root = qgis_prefix.parent.parent
    qt_root = qgis_root / "apps" / "Qt5"
    grass_root = qgis_root / "apps" / "grass" / "grass84"

    os.environ["OSGEO4W_ROOT"] = str(qgis_root)
    os.environ["QGIS_PREFIX_PATH"] = str(qgis_prefix).replace("\\", "/")
    os.environ["GDAL_FILENAME_IS_UTF8"] = "YES"
    os.environ["VSI_CACHE"] = "TRUE"
    os.environ["VSI_CACHE_SIZE"] = "1000000"
    os.environ["PYTHONUTF8"] = "1"

    _set_env_path("GDAL_DATA", qgis_root / "apps" / "gdal" / "share" / "gdal")
    _set_env_path(
        "GDAL_DRIVER_PATH",
        qgis_root / "apps" / "gdal" / "lib" / "gdalplugins",
    )
    _set_env_path("PROJ_DATA", qgis_root / "share" / "proj")
    _set_env_path("PROJ_LIB", qgis_root / "share" / "proj")
    _set_env_path("OPENSSL_ENGINES", qgis_root / "lib" / "engines-3")
    _set_env_path("SSL_CERT_FILE", qgis_root / "bin" / "curl-ca-bundle.crt")
    _set_env_path("SSL_CERT_DIR", qgis_root / "apps" / "openssl" / "certs")
    _set_env_path("PDAL_DRIVER_PATH", qgis_root / "apps" / "pdal" / "plugins")
    _set_env_path("GS_LIB", qgis_root / "apps" / "gs" / "lib")

    qt_plugin_paths = [
        qgis_prefix / "qtplugins",
        qt_root / "plugins",
    ]
    _set_env_paths("QT_PLUGIN_PATH", qt_plugin_paths)
    _set_env_path("QT_QPA_PLATFORM_PLUGIN_PATH", qt_root / "plugins" / "platforms")

    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    dll_paths = [
        qgis_prefix / "bin",
        qgis_root / "apps" / "Python312" / "Scripts",
        qt_root / "bin",
        grass_root / "lib",
        grass_root / "bin",
        qgis_root / "bin",
    ]
    _set_env_paths(
        "PATH",
        dll_paths
        + [
            windir / "system32",
            windir,
            windir / "system32" / "WBem",
        ],
    )
    _add_dll_directories(dll_paths)

    python_paths = [
        SRC_ROOT,
        REPO_ROOT,
        qgis_prefix / "python",
        qgis_prefix / "python" / "plugins",
        grass_root / "etc" / "python",
    ]
    _prepend_sys_path(python_paths)
    _set_env_paths("PYTHONPATH", python_paths)


configure_qgis_environment()

# --- IMPORTANT: CONFIGURATION SECTION ---
# You MUST provide paths to some real test data here for the dialog to work.
# Replace these placeholder paths with actual paths to a DEM and a points shapefile on your system.
DEM_PATH = r"E:\8 WRE Research\5 mHM\2 Pymhm Plugin Development\pymhm Test Basins\Rapti\mhm-qgis\<domain>\.temp\Morphology\1_dem_filled.tif"  # <--- EDIT THIS: Path to a test DEM
POUR_POINTS_PATH = r"E:\8 WRE Research\5 mHM\2 Pymhm Plugin Development\pymhm Test Basins\Rapti\0 GIS\2_pour_point.gpkg"  # <--- EDIT THIS: Path to a test pour points shapefile

# This ensures that the script can find the 'mhm_qgis' package and its modules
# It assumes this script is in src/mhm_qgis/test/ and adds the parent directory (src/mhm_qgis/) to the path.
plugin_dir = PLUGIN_PACKAGE_DIR
# ----------------------------------------

from qgis.core import QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer


def main():
    """
    Main function to initialize QGIS, load test data, and run the dialog.
    """
    print("--- Initializing QGIS Application for standalone testing ---")

    # 1. Initialize the QGIS Application. This is crucial for any QGIS-related code.
    qgis_prefix = os.environ.get("QGIS_PREFIX_PATH")
    if not qgis_prefix:
        raise RuntimeError(
            "QGIS_PREFIX_PATH environment variable not set. Ensure you are running this with the correct debugger configuration or that QGIS is installed at the expected path."
        )

    QgsApplication.setPrefixPath(qgis_prefix, True)

    # We use QgsApplication.instance() which might return an existing instance if run inside QGIS,
    # or we create a new one. The 'True' indicates it's a GUI-enabled application.
    qgs_app = QgsApplication.instance()
    if qgs_app is None:
        qgs_app = QgsApplication([], True)

    qgs_app.initQgis()
    print(f"QGIS Prefix Path set to: {QgsApplication.prefixPath()}")
    print("QGIS Initialized.")

    # Import plugin UI after QGIS has a prefix path and provider registry.
    from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog

    # 2. Get a reference to the project instance. This is where layers are managed.
    project = QgsProject.instance()

    # 3. Load dummy/test layers into the project.
    # This will populate the QgsMapLayerComboBox widgets in your dialog.
    print(f"Loading test DEM: {DEM_PATH}")
    dem_layer = QgsRasterLayer(DEM_PATH, "Test DEM")
    if not dem_layer.isValid():
        print(
            f"ERROR: Failed to load test DEM layer. Please check the path: {DEM_PATH}"
        )
    else:
        project.addMapLayer(dem_layer)
        print("Test DEM loaded successfully.")

    print(f"Loading test Pour Points: {POUR_POINTS_PATH}")
    points_layer = QgsVectorLayer(POUR_POINTS_PATH, "Test Pour Points", "ogr")
    if not points_layer.isValid():
        print(
            f"ERROR: Failed to load test pour points layer. Please check the path: {POUR_POINTS_PATH}"
        )
    else:
        project.addMapLayer(points_layer)
        print("Test Pour Points loaded successfully.")

    print("\n--- Starting the MhmQgisDialog ---")
    # 4. Create an instance of your dialog and show it.
    dialog = MhmQgisDialog()
    dialog.show()

    # 5. Run the Qt event loop. This makes the dialog interactive.
    # The script will pause here until you close the dialog.
    qgs_app.exec_()

    print("\n--- Dialog closed, cleaning up. ---")
    # 6. Clean up the QGIS application environment.
    qgs_app.exitQgis()
    print("QGIS environment closed.")


if __name__ == "__main__":
    main()
