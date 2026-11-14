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
    assert dialog.pushButton_browseL0File.toolTip() == str(dem)
    assert dialog.label_nColsL0File.text() == "4"
    assert not dialog.toolButton_descriptionL0File.isEnabled()  # nothing to describe before Set
    assert "eye-off.svg" in dialog.label_displayL0File.text()  # a loaded file can be shown
    assert dialog.label_displayL1File.text() == ""
    _browse(dialog, monkeypatch, "L2", folder / "wanted.nc", "pre")
    assert (dialog.spinBox_L2ResolutionMultiplier.value(), dialog.lineEdit_L2Resolution.text()) == (2, repr(1.0))
    assert dialog.label_fileL2ResolutionMultiplier.text() == "2"
    for level in ("L1", "L11"):
        getattr(dialog, f"spinBox_{level}ResolutionMultiplier").setValue(2)
    dialog.checkBox_extentL2enforce.setChecked(True)
    dialog.pushButton_setLatLon.click()
    assert not warnings
    assert (dialog.label_nColsL0Common.text(), dialog.label_nColsL2Common.text()) == ("4", "2")
    assert dialog.toolButton_descriptionL2File.isEnabled()
    assert dialog.toolButton_descriptionL2File.toolTip() == "Used as is."
    from mhm_qgis.qgis_bridge.display import grid as grid_display

    drawn = []
    monkeypatch.setattr(grid_display, "display_grids", lambda _dialog, levels: drawn.append(levels) or ["mHM L2 grid"])
    assert "eye-off.svg" in dialog.label_displayL2GridCommon.text()
    dialog.label_displayL2GridCommon.linkActivated.emit("grid:L2")  # its eye
    assert (drawn, dialog._displayed) == ([["L2"]], {"grid:L2": "mHM L2 grid"})
    assert "eye.svg" in dialog.label_displayL2GridCommon.text()
    dialog.label_displayL2GridCommon.linkActivated.emit("grid:L2")
    assert "eye-off.svg" in dialog.label_displayL2GridCommon.text()  # hidden again
    assert (tmp_path / "data" / "latlon.nc").is_file() or list(tmp_path.rglob("latlon.nc"))
    common = grid.require_common_grid(dialog)
    assert (common["l2_ratio_to_l0"], common["study_bounds"]) == (2, [9.5, 11.5, 49.5, 51.5])
    assert processing.workflow(tmp_path, "execute_all_meteo")["status"] == "pending"

    dialog.input_combo("dem").setLayer(QgsRasterLayer(str(dem), "DEM"))
    dialog.pushButton_setDEM.click()
    assert dialog._dem_error is None
    assert dialog.label_demDescription.text() == "Used as is."

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


def test_the_reference_dialogs_browse_button_reads_the_picked_raster(tmp_path, monkeypatch):
    from mhm_qgis.qt.dialogs import browse_resolution_L2_L1_L11_file as module
    from mhm_qgis.qt.dialogs.browse_resolution_L0_file import L0FileDialog

    dem = tmp_path / "dem.tif"
    _geotiff(dem, 4, 4, (9.5, .5, 0, 51.5, 0, -.5), 4326)
    monkeypatch.setattr(module, "open_file", lambda *_args: (str(dem), "Rasters (*.tif *.tiff *.asc *.nc)"))
    prompt = L0FileDialog("L0", QgsCoordinateReferenceSystem("EPSG:4326"))
    prompt.pushButton_browseRasterInput.click()
    assert prompt.comboBox_rasterInput.currentText() == str(dem)
    assert (prompt.label_rasterResolutionValue.text(), prompt.error) == ("0.5", "")
    prompt.close()


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
    assert common["study_bounds"] == [9.0, 12.0, 49.0, 52.0]  # whole L2 cells around the DEM
    assert dialog.label_nColsL0Common.text().startswith('<span style="color:#1565c0">6</span>')
    assert dialog.label_nColsL2Common.text() == "3"  # as the file
    note = dialog._header_notes["L0:ncols"]
    assert "intersection of the ticked extents (L0), snapped outward" in note
    assert note.endswith("6 columns of 0.5 instead of the L0 file's 4 of 0.5.\n"
                         "Padded with no data: 1 column west, 1 column east.")
    description = dialog.toolButton_descriptionL0File.toolTip()
    assert description == "Padded with no data: 1 column west, 1 column east, 1 row south, 1 row north."
    assert dialog.toolButton_descriptionL2File.toolTip() == "Used as is."
    shown = []
    monkeypatch.setattr(grid.QMessageBox, "information", lambda _parent, _title, text: shown.append(text))
    dialog.label_nColsL0Common.linkActivated.emit("L0:ncols")  # the ⓘ
    dialog.toolButton_descriptionL0File.click()
    assert shown == [note, description]
    dialog.checkBox_extentL0enforce.setChecked(False)
    assert not dialog.toolButton_descriptionL0File.isEnabled()  # an input changed: Set again
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
    assert grid.require_common_grid(dialog)["l0_header"]["ncols"] == 4  # 7 cells / 2, outward
    dialog.input_combo("dem").setLayer(QgsRasterLayer(str(dem), "DEM"))
    dialog.pushButton_setDEM.click()
    assert len(warnings) == 1
    assert "set the upscale factor to L0 / DEM cell" in warnings[0]
    dialog.groupBox_upscaleTopography.setChecked(True)
    dialog.spinBox_upscaleTopographyFactor.setValue(2)
    dialog.pushButton_setDEM.click()
    assert dialog._dem_error is None
    assert dialog.label_demDescription.text() == (  # in the DEM's own 100 m cells
        "Topography upscaled x 2 (mhm-tools).\nPadded with no data: 1 column east, 1 row north.")
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


def test_the_steps_say_what_is_cropped_and_what_is_added():
    from mhm_qgis.core.grid import Extent

    assert grid._edges(Extent(0, 10, 0, 10), Extent(2, 12, 0, 9.5), 1, 0.01) == [
        "Cropped: 2 columns west, 0.5 rows north.", "Padded with no data: 2 columns east."]


def test_the_snapped_extent_must_stay_inside_the_meteo_grid(tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(grid.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    dem = tmp_path / "dem.tif"
    _geotiff(dem, 4, 4, (9.5, .5, 0, 51.5, 0, -.5), 4326)
    folder = tmp_path / "precipitation"
    _precipitation(folder, [50.75, 50.25], [10.25, 10.75])  # 0.5-degree cells: 10..11 x 50..51, inside the DEM
    dialog = _open(tmp_path)
    _browse(dialog, monkeypatch, "L0", dem)
    _browse(dialog, monkeypatch, "L2", folder / "wanted.nc", "pre")
    for level in ("L1", "L11"):
        getattr(dialog, f"spinBox_{level}ResolutionMultiplier").setValue(2)
    dialog.pushButton_setLatLon.click()  # L0 ticked: 1-degree blocks on the meteo lattice, 9..12 x 49..52
    assert len(warnings) == 1
    assert "reaches beyond the L2 (meteo) grid: 2 cells west, 2 cells east, 2 cells south, 2 cells north" in warnings[0]
    dialog.checkBox_extentL2enforce.setChecked(True)  # the meteo extent bounds it now
    dialog.pushButton_setLatLon.click()
    assert grid.require_common_grid(dialog)["study_bounds"] == [10.0, 11.0, 50.0, 51.0]
    dialog.close()
