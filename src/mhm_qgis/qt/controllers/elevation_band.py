"""UI adapter for the QGIS-free elevation-band API."""
from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtWidgets import QDialog

from ...core.handlers.store import registry
from ...core.handlers.store.paths import temp_morphology_folder
from ...core.morphology import elevation_bands
from ...core.utils.report import CRITICAL, INFORMATION, WARNING
from ...qgis_bridge import layers as qgis_layers
from ..reporting import dialog_reporter
from ..ui import load_form
from ..workflows import hydrology

Ui_ElevationBandDialog = load_form("elevation_band_dialog.ui")


def ask_elevation_band_width(
    dialog,
    min_elevation: float,
    max_elevation: float,
) -> float | None:
    """Show the selected range and ask for the elevation window width."""
    band_dialog = QDialog(dialog)
    ui = Ui_ElevationBandDialog()
    ui.setupUi(band_dialog)
    ui.min_elevation_value.setText(f"{min_elevation:.2f}")
    ui.max_elevation_value.setText(f"{max_elevation:.2f}")

    elevation_range = max(max_elevation - min_elevation, 1.0)
    suggested_width = elevation_bands.nice_step(elevation_range / 10.0)
    ui.width_spin_box.setMaximum(max(elevation_range * 10.0, suggested_width))
    ui.width_spin_box.setSingleStep(max(suggested_width / 10.0, 0.001))
    ui.width_spin_box.setValue(suggested_width)
    return ui.width_spin_box.value() if band_dialog.exec() == QDialog.DialogCode.Accepted else None


def _report(dialog, level: str, title: str, message: str) -> None:
    dialog_reporter(dialog, log=dialog.log_message)(level, title, message)


def _local_raster_source(layer) -> str | None:
    source = qgis_layers.raster_source(layer)
    if not source:
        return None
    candidate = source.split("|", 1)[0]
    return candidate if Path(candidate).is_file() else source


def _ensure_elevation_prerequisites(dialog) -> tuple[str, tuple[Path, ...]] | None:
    """Return the API-owned prerequisites for elevation-band processing."""
    try:
        filled_dem = hydrology.prepare_filled_dem(dialog)
    except Exception as error:
        _report(dialog, WARNING, "Missing DEM", str(error))
        return None

    watershed_folder = Path(temp_morphology_folder(dialog.project_folder)) / "Watersheds"
    rasters = elevation_bands.collect_watershed_rasters(watershed_folder)
    if not rasters:
        _report(
            dialog,
            WARNING,
            "Missing Watersheds",
            "No subcatchment watershed rasters were found. Save domains first.",
        )
        return None
    return filled_dem, rasters


def process_elevation_bands(dialog) -> None:
    """Collect UI inputs, run the core elevation-band command, and display outputs."""
    dialog.log_message("\n--- Creating Elevation Bands ---")
    if not dialog.check_prerequisites(needs_pour_points=True):
        return
    dem_layer = dialog.input_combo("dem").currentLayer()
    dem_path = _local_raster_source(dem_layer)
    if not dem_path:
        _report(dialog, WARNING, "Input Error", "Please select a DEM raster layer.")
        return

    try:
        selected_min, selected_max = elevation_bands.raster_value_range(dem_path)
    except (OSError, RuntimeError, ValueError) as error:
        _report(dialog, CRITICAL, "Input Error", str(error))
        return
    width = ask_elevation_band_width(dialog, selected_min, selected_max)
    if width is None:
        dialog.log_message("Elevation band creation cancelled by user.")
        return

    prerequisites = _ensure_elevation_prerequisites(dialog)
    if prerequisites is None:
        return
    filled_dem, watershed_rasters = prerequisites
    folder = Path(temp_morphology_folder(dialog.project_folder)) / "ElevationBands"
    try:
        result = elevation_bands.create_elevation_bands(
            filled_dem,
            watershed_rasters,
            folder,
            width,
            log=dialog.log_message,
        )
    except (OSError, RuntimeError, ValueError) as error:
        _report(dialog, CRITICAL, "Elevation Bands", str(error))
        return

    dialog.log_message(
        f"Selected DEM elevation range: {selected_min:.2f} to {selected_max:.2f}"
    )
    dialog.log_message(
        f"Rounded band range used: {result.minimum:.2f} to {result.maximum:.2f} "
        f"({result.count} bands, window width {width:.2f})"
    )
    for path in result.rasters:
        name = path.stem.replace("elevation_bands_", "Elevation_Bands_", 1)
        qgis_layers.load(path, name, log=dialog.log_message)
        registry.register(dialog.project_folder, path, name=name, loaded=True)
    registry.register(
        dialog.project_folder,
        result.summary,
        name=result.summary.name,
        loaded=False,
    )
    dialog.log_message(f"Elevation band CSV written: {result.summary}")
    _report(
        dialog,
        INFORMATION,
        "Elevation Bands Created",
        f"Created {len(result.rasters)} elevation band raster(s).\n{result.summary}",
    )


def process_band_details(dialog) -> None:
    """Collect the land-cover input and write elevation-band detail areas."""
    dialog.log_message("\n--- Creating Elevation Band Land Cover Details ---")
    if not dialog.project_folder:
        _report(
            dialog,
            CRITICAL,
            "Missing Input",
            "Please select a project folder before proceeding.",
        )
        return

    folder = Path(temp_morphology_folder(dialog.project_folder)) / "ElevationBands"
    if not elevation_bands.collect_elevation_band_rasters(folder):
        dialog.log_message(
            "Elevation-band rasters are missing. Running Elevation Bands first..."
        )
        process_elevation_bands(dialog)
    if not elevation_bands.collect_elevation_band_rasters(folder):
        _report(
            dialog,
            WARNING,
            "Missing Elevation Bands",
            "No elevation band rasters were found or created.",
        )
        return

    source = (dialog.categorical_lookup_config("lc") or {}).get("input_path", "")
    land_cover_path = _local_raster_source(
        qgis_layers.open_layer(source, "Land cover", is_raster=True)
    )
    if not land_cover_path:
        _report(
            dialog,
            WARNING,
            "Input Error",
            "Please select the original land-cover raster for band details.",
        )
        return

    names = elevation_bands.read_land_cover_class_names(
        dialog.categorical_lookup_config("lc"),
        log=dialog.log_message,
    )
    try:
        result = elevation_bands.create_band_land_cover_details(
            land_cover_path,
            folder,
            class_names=names,
            log=dialog.log_message,
        )
    except (OSError, RuntimeError, ValueError) as error:
        _report(dialog, CRITICAL, "Band Details", str(error))
        return

    if result.aligned_land_cover is not None:
        registry.register(
            dialog.project_folder,
            result.aligned_land_cover,
            name=result.aligned_land_cover.name,
            loaded=False,
        )
    registry.register(
        dialog.project_folder,
        result.report,
        name=result.report.name,
        loaded=False,
    )
    dialog.log_message(f"Elevation band land-cover details CSV written: {result.report}")
    _report(
        dialog,
        INFORMATION,
        "Band Details Created",
        f"Created {result.row_count} band detail row(s).\n{result.report}",
    )
