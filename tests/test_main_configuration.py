"""Real-QGIS configuration: the Resolution page, the DEM check and forcing scans."""
import time
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

pytest.importorskip("qgis.core")
from osgeo import gdal, osr
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsRasterLayer

from mhm_qgis.core.handlers.state import nml_settings, processing
from mhm_qgis.core.meteorology.forcing import MeteoFolderSpec
from mhm_qgis.qt.controllers import grid, main, project
from mhm_qgis.qt.dialogs.meteo_var_details_input import MeteoVarDetailsDialog
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog


def _geotiff(path, cols, rows, transform, epsg):
    raster = gdal.GetDriverByName("GTiff").Create(str(path), cols, rows, 1, gdal.GDT_Float32)
    raster.SetGeoTransform(transform)
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(epsg)
    raster.SetProjection(crs.ExportToWkt())
    raster.GetRasterBand(1).WriteArray(np.arange(cols * rows, dtype="float32").reshape(rows, cols) + 1)


def _precipitation(folder, lat, lon):
    folder.mkdir(exist_ok=True)
    xr.Dataset({"pre": (("time", "lat", "lon"), np.ones((3, len(lat), len(lon))), {"units": "mm"})},
               coords={"time": np.array(["2001-01-01", "2001-01-02", "2001-01-03"], dtype="datetime64[ns]"),
                       "lat": lat, "lon": lon}).to_netcdf(folder / "wanted.nc")


def _open(tmp_path, epsg=4326):
    dialog = MhmQgisDialog()
    dialog.setup_folder = str(tmp_path)
    dialog.mProjectionSelectionWidget_crs.setCrs(QgsCoordinateReferenceSystem(f"EPSG:{epsg}"))
    dialog.pushButton_setCRS.click()  # only a Set CRS is kept
    return dialog


def _browse(dialog, monkeypatch, level, path, variable=None):
    from mhm_qgis.qt.dialogs.browse_resolution_L0_file import L0FileDialog
    from mhm_qgis.qt.dialogs.browse_resolution_L2_L1_L11_file import LevelFileDialog

    def pick(prompt):
        prompt.comboBox_rasterInput.setCurrentText(str(path))
        if variable:
            prompt.comboBox_varName.setCurrentText(variable)
        return 1 if prompt.info and not prompt.error else 0

    monkeypatch.setattr(L0FileDialog if level == "L0" else LevelFileDialog, "exec", pick)
    getattr(dialog, f"pushButton_browse{level}File").click()


def _wait(dialog, key):
    deadline = time.monotonic() + 15
    while dialog.task_coordinator.is_busy(key) and time.monotonic() < deadline:
        QgsApplication.processEvents()
        time.sleep(.01)
    assert not dialog.task_coordinator.is_busy(key)


def test_reference_files_set_the_common_grid_the_dem_and_precipitation_cover(tmp_path, monkeypatch):
    from mhm_qgis.qt import project_tree
    from mhm_qgis.qt.controllers import forcing

    warnings = []
    for module in (main, grid, forcing):
        monkeypatch.setattr(module.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    dem = tmp_path / "dem.tif"
    _geotiff(dem, 4, 4, (9.5, .5, 0, 51.5, 0, -.5), 4326)
    folder = tmp_path / "precipitation"
    _precipitation(folder, [51., 50.], [10., 11.])
    dialog = _open(tmp_path)

    _browse(dialog, monkeypatch, "L0", dem)
    assert dialog.lineEdit_L0Resolution.text() == repr(0.5)
    assert (dialog.label_descriptionL0File.text(), dialog.label_nColsL0File.text()) == ("dem.tif", "4")
    _browse(dialog, monkeypatch, "L2", folder / "wanted.nc", "pre")
    assert (dialog.spinBox_L2ResolutionMultiplier.value(), dialog.lineEdit_L2Resolution.text()) == (2, repr(1.0))
    assert dialog.label_fileL2ResolutionMultiplier.text() == "2"
    for level in ("L1", "L11"):
        getattr(dialog, f"spinBox_{level}ResolutionMultiplier").setValue(2)
    dialog.checkBox_extentL2enforce.setChecked(True)
    dialog.pushButton_setLatLon.click()
    assert not warnings
    assert (dialog.label_nColsL0Common.text(), dialog.label_nColsL2Common.text()) == ("4", "2")
    assert dialog.label_descriptionL2File.text() == "used as is"
    from mhm_qgis.qgis_bridge.display import grid as grid_display

    drawn = []
    monkeypatch.setattr(grid_display, "display_grids", lambda _dialog, levels: drawn.append(levels))
    dialog.label_displayL2GridCommon.linkActivated.emit("L2")  # its 👁
    assert drawn == [["L2"]]
    assert (tmp_path / "data" / "latlon.nc").is_file() or list(tmp_path.rglob("latlon.nc"))
    common = grid.require_common_grid(dialog)
    assert (common["l2_ratio_to_l0"], common["study_bounds"]) == (2, [9.5, 11.5, 49.5, 51.5])
    assert processing.workflow(tmp_path, "execute_all_meteo")["status"] == "pending"

    dialog.input_combo("dem").setLayer(QgsRasterLayer(str(dem), "DEM"))
    dialog.pushButton_setDEM.click()
    assert dialog._dem_error is None
    assert dialog.label_demDescription.text().startswith("covers the common extent")

    dialog.treeWidget_projectStructure.setCurrentItem(project_tree.item(dialog, "meteo_pre"))
    forcing.submit_meteo_input(dialog, "pre", MeteoFolderSpec("pre", folder, unit="mm", pattern="wanted*.nc"))
    _wait(dialog, "meteo-scan-pre")
    assert not warnings
    assert dialog.label_precipitationStartTimeValue.text().startswith("2001-01-01")
    assert dialog.dateTimeEdit_simulationEndTimeInput.dateTime().toString("yyyy-MM-dd") == "2001-01-03"
    assert "copied" in forcing.note(dialog, "pre")
    assert "copied" in main.form_on(dialog, "precipitation").label_description.text()
    assert not nml_settings.load_settings(tmp_path).get("time")  # only Set Periods saves it
    dialog.pushButton_setSimulationPeriod.click()
    assert nml_settings.load_settings(tmp_path)["time"]["sim_end"] == "2001-01-03T00:00"

    dialog.spinBox_L1ResolutionMultiplier.setValue(4)
    assert dialog.label_nColsL0Common.text() == ""  # Set again
    with pytest.raises(ValueError, match="Set the resolutions"):
        grid.require_common_grid(dialog)
    dialog.spinBox_L1ResolutionMultiplier.setValue(2)
    assert grid.grid_current(dialog)
    dialog.save_input_state()
    restored = MhmQgisDialog()
    restored.setup_folder = str(tmp_path)
    restored.load_input_state()
    assert restored.lineEdit_L0Resolution.text() == repr(0.5)
    assert restored.checkBox_extentL2enforce.isChecked()
    assert restored.label_nColsL2Common.text() == "2"  # the Set grid still applies
    assert restored._dem_error is None
    assert restored._meteo_inspections == {}  # Reload never scans the folders.
    restored.close()
    dialog.close()


def test_a_value_changed_from_its_file_is_blue_with_the_reason(tmp_path, monkeypatch):
    dem = tmp_path / "dem.tif"
    _geotiff(dem, 4, 4, (9.5, .5, 0, 51.5, 0, -.5), 4326)
    folder = tmp_path / "precipitation"
    _precipitation(folder, [51.5, 50.5, 49.5], [9.5, 10.5, 11.5])  # 9..12 x 49..52: wider than the DEM
    dialog = _open(tmp_path)
    _browse(dialog, monkeypatch, "L0", dem)
    _browse(dialog, monkeypatch, "L2", folder / "wanted.nc", "pre")
    for level in ("L1", "L11"):
        getattr(dialog, f"spinBox_{level}ResolutionMultiplier").setValue(2)
    dialog.pushButton_setLatLon.click()  # only L0's extent ticked
    common = grid.require_common_grid(dialog)
    assert common["study_bounds"] == [10.0, 11.0, 50.0, 51.0]  # whole L2 cells inside the DEM
    assert dialog.label_nColsL2Common.text().startswith('<span style="color:#1565c0">1</span>')
    assert "intersection of the ticked extents (L0)" in dialog._header_notes["L2:ncols"]
    assert "edge cells cropped" in dialog.label_descriptionL2File.text()
    shown = []
    monkeypatch.setattr(grid.QToolTip, "showText", lambda _pos, text, _widget: shown.append(text))
    dialog.label_nColsL2Common.linkActivated.emit("L2:ncols")
    assert shown == [dialog._header_notes["L2:ncols"]]
    dialog.checkBox_extentL0enforce.setChecked(False)
    warnings = []
    monkeypatch.setattr(grid.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    dialog.pushButton_setLatLon.click()
    assert warnings == ["Tick at least one extent in the Extent row: the common extent lies inside every ticked file."]
    dialog.close()


def test_a_dem_off_the_l0_grid_is_refused_with_the_reason(tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(grid.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    dem = tmp_path / "dem.tif"
    _geotiff(dem, 7, 5, (0, 100, 0, 500, 0, -100), 32632)
    dialog = _open(tmp_path, 32632)
    _browse(dialog, monkeypatch, "L0", dem)
    dialog.lineEdit_L0Resolution.setText("230")
    dialog.lineEdit_L0Resolution.editingFinished.emit()  # the nearest whole factor of the file's cell
    assert (dialog.lineEdit_L0Resolution.text(), dialog._resolution_files["L0"]["factor"]) == (repr(200.0), 2)
    for level in ("L1", "L11", "L2"):
        getattr(dialog, f"lineEdit_{level}Resolution").setText("200")
    dialog.pushButton_setLatLon.click()
    assert not warnings
    assert grid.require_common_grid(dialog)["l0_header"]["ncols"] == 3  # 7 cells / 2, inward
    dialog.input_combo("dem").setLayer(QgsRasterLayer(str(dem), "DEM"))
    dialog.pushButton_setDEM.click()
    assert len(warnings) == 1
    assert "set the upscale factor to L0 / DEM cell" in warnings[0]
    dialog.groupBox_upscaleTopography.setChecked(True)
    dialog.spinBox_upscaleTopographyFactor.setValue(2)
    dialog.pushButton_setDEM.click()
    assert dialog._dem_error is None
    assert "topography upscaled x 2" in dialog.label_demDescription.text()
    dialog.close()


def test_create_structure_opens_lazy_tree(tmp_path, monkeypatch):
    from mhm_qgis.qt.dialogs.project_folder_tree import ProjectFolderTreeDialog
    opened = []
    def show(tree):
        opened.append(tree.model.filePath(tree.treeView_projectFolder.rootIndex()))
        return 0
    monkeypatch.setattr(ProjectFolderTreeDialog, "exec", show)
    dialog = MhmQgisDialog()
    from mhm_qgis.core.handlers.store.paths import domain_folder
    from mhm_qgis.qt.dialogs.domain_folder_name import DomainFolderNameDialog
    dialog.project_directory = str(tmp_path)
    project.populate_domains(dialog)
    def name(prompt):
        prompt.path = domain_folder(tmp_path, "Alpine")
        return prompt.DialogCode.Accepted
    monkeypatch.setattr(DomainFolderNameDialog, "exec", name)
    dialog.pushButton_createOrLoadDomainStructure.click()
    root = tmp_path / "mhm-qgis/Alpine"
    assert opened == [str(root)]
    from mhm_qgis.core.handlers.store.layout import morph_folder
    assert Path(morph_folder(root)).is_dir()
    assert (root / "settings.yaml").is_file()
    assert (root / ".temp" / "mhm_qgis_input_state.json").is_file()
    assert processing.workflow(root, "project_structure")["status"] == "completed"
    assert "#a5d6a7" in dialog.pushButton_createOrLoadDomainStructure.styleSheet()
    assert not dialog.comboBox_mHMversion.isEnabled()
    dialog.comboBox_domainFolderSelector.setCurrentIndex(
        dialog.comboBox_domainFolderSelector.count() - 1
    )
    assert dialog.comboBox_mHMversion.isEnabled()
    assert not (tmp_path / "settings.yaml").exists()
    dialog.close()


def test_a_failed_scan_from_the_popup_keeps_the_entered_values(tmp_path, monkeypatch):
    from mhm_qgis.qt.controllers import forcing

    warnings, seen = [], []
    monkeypatch.setattr(forcing.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    source = tmp_path / "source"
    source.mkdir()
    (source / "bad.nc").write_text("not a NetCDF file")

    def enter(prompt):
        prompt.comboBox_inputFolder.setCurrentText(str(source))
        prompt.lineEdit_fileNameGlobDefinition.setText("bad*.nc")
        prompt.accept()
        return prompt.result()

    dialog = MhmQgisDialog()
    dialog.setup_folder = str(tmp_path)
    monkeypatch.setattr(MeteoVarDetailsDialog, "exec", enter)
    forcing.edit_meteo_input(dialog, "tavg")
    deadline = time.monotonic() + 15
    while dialog.task_coordinator.is_busy() and time.monotonic() < deadline:
        QgsApplication.processEvents()
        time.sleep(.01)
    assert warnings  # the scan failed
    assert "Traceback" not in warnings[0]
    assert not warnings[0].startswith("https://")
    monkeypatch.setattr(MeteoVarDetailsDialog, "exec", lambda prompt: seen.append(prompt.draft()) or 0)
    forcing.edit_meteo_input(dialog, "tavg")
    assert (seen[0]["folder"], seen[0]["pattern"]) == (str(source), "bad*.nc")
    dialog.close()
