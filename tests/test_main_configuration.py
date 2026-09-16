"""Real-QGIS configuration and explicit precipitation scanning."""
import time
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

pytest.importorskip("qgis.core")
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsRasterLayer
from osgeo import gdal, osr

from mhm_qgis.qt.controllers import main
from mhm_qgis.qt.dialogs.mhm_qgis_main import MhmQgisDialog
from mhm_qgis.core.handlers.state import nml_settings, processing
from mhm_qgis.core.meteorology.forcing import MeteoFolderSpec
from mhm_qgis.qt.dialogs.meteo_var_details_input import MeteoVarDetailsDialog


def test_scan_controls_grid_default_override_dates_and_restore(tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(main.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    dem = tmp_path / "dem.tif"
    raster = gdal.GetDriverByName("GTiff").Create(str(dem), 4, 4, 1, gdal.GDT_Float32)
    raster.SetGeoTransform((9.5, .5, 0, 51.5, 0, -.5))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(4326)
    raster.SetProjection(crs.ExportToWkt())
    raster.GetRasterBand(1).WriteArray(np.arange(16).reshape(4, 4) + 1)
    raster = None
    folder = tmp_path / "precipitation"
    folder.mkdir()
    xr.Dataset({"pre": (("time", "lat", "lon"), np.ones((3, 2, 2)), {"units": "mm"})},
               coords={"time": np.array(["2001-01-01", "2001-01-02", "2001-01-03"], dtype="datetime64[ns]"),
                       "lat": [51., 50.], "lon": [10., 11.]}).to_netcdf(folder / "wanted.nc")
    (folder / "unrelated.nc").write_text("ignore me")
    dialog = MhmQgisDialog()
    dialog.project_folder = str(tmp_path)
    dialog._loading_input_state = True
    dialog.mProjectionSelectionWidget_crs.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    dialog.input_combo("dem").setLayer(QgsRasterLayer(str(dem), "DEM"))
    dialog.refresh_meteo_folder_sources()
    dialog.comboBox_precipitationFile.setCurrentIndex(dialog._folder_combo_index(dialog.comboBox_precipitationFile, str(folder)))
    dialog.lineEdit_precipitationGlobDefinition.setText("wanted*.nc")
    dialog._loading_input_state = False
    assert dialog._meteo_inspections.get("precipitation") is None
    assert not list(tmp_path.rglob("dem_derivatives.nc"))
    def accept(prompt):
        assert prompt.groupBox.title() == "pre"
        prompt.spec = MeteoFolderSpec("pre", folder, unit="mm", pattern="wanted*.nc")
        return prompt.DialogCode.Accepted
    monkeypatch.setattr(MeteoVarDetailsDialog, "exec", accept)
    dialog.pushButton_scanPrecipitationFiles.click()
    deadline = time.monotonic() + 15
    while dialog.task_coordinator.is_busy("meteo-scan-pre") and time.monotonic() < deadline:
        QgsApplication.processEvents()
        time.sleep(.01)
    assert not dialog.task_coordinator.is_busy("meteo-scan-pre")
    assert not warnings
    assert dialog.spinBox_L2ResolutionMultiplier.value() == 2
    assert dialog.label_precipitationResolutionValue.text() == "1"
    assert dialog.label_precipitationStartTimeValue.text().startswith("2001-01-01")
    grid = main.require_common_grid(dialog)
    assert grid["l0_header"]["ncols"] == 4
    assert grid["l2_header"]["ncols"] == 2
    assert not list(tmp_path.rglob("dem_derivatives.nc"))
    assert nml_settings.load_settings(tmp_path)["time"]["sim_end"] == "2001-01-03T00:00"
    dialog.spinBox_L2ResolutionMultiplier.setValue(3)
    assert main.require_common_grid(dialog)["l2_ratio_to_l0"] == 3
    main.precipitation_scanned(dialog, dialog._meteo_inspections["pre"])
    assert dialog.spinBox_L2ResolutionMultiplier.value() == 3
    dialog.save_input_state()
    restored = MhmQgisDialog()
    restored.project_folder = str(tmp_path)
    restored.load_input_state()
    assert restored.lineEdit_precipitationGlobDefinition.text() == "wanted*.nc"
    assert restored.spinBox_L2ResolutionMultiplier.value() == 3
    assert main.require_common_grid(restored)["l2_ratio_to_l0"] == 3
    assert restored._meteo_inspections == {}  # Reload never scans the folders.
    restored.lineEdit_precipitationGlobDefinition.setText("none*.nc")
    assert not processing.section(tmp_path, "grid")
    with pytest.raises(ValueError, match="Scan precipitation"):
        main.require_common_grid(restored)
    restored.close()
    dialog.close()


def test_create_structure_opens_lazy_tree(tmp_path, monkeypatch):
    from mhm_qgis.qt.dialogs.project_folder_tree import ProjectFolderTreeDialog
    opened = []
    def show(tree):
        opened.append(tree.model.filePath(tree.treeView_projectFolder.rootIndex()))
        return 0
    monkeypatch.setattr(ProjectFolderTreeDialog, "exec", show)
    dialog = MhmQgisDialog()
    from mhm_qgis.qt.dialogs.domain_folder_name import DomainFolderNameDialog
    from mhm_qgis.core.handlers.store.paths import domain_folder
    dialog.project_directory = str(tmp_path)
    main.populate_domain_folders(dialog)
    def name(prompt):
        prompt.path = domain_folder(tmp_path, "Alpine")
        return prompt.DialogCode.Accepted
    monkeypatch.setattr(DomainFolderNameDialog, "exec", name)
    dialog.pushButton_createProjectStructure.click()
    root = tmp_path / "mhm-qgis/Alpine"
    assert opened == [str(root)]
    from mhm_qgis.core.handlers.store.layout import morph_folder
    assert Path(morph_folder(root)).is_dir()
    assert (root / "settings.yaml").is_file()
    assert (root / "mhm_qgis_input_state.json").is_file()
    assert not (root / "data/master").exists()
    assert not (tmp_path / "settings.yaml").exists()
    assert not (tmp_path / "mhm-plugin").exists()
    dialog.close()
