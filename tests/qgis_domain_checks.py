"""Small offscreen integration check; executed by test_domain_qgis_runtime."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from importlib.util import find_spec

if find_spec("qgis") is None:
    raise SystemExit(77)

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFeature,
                       QgsGeometry, QgsVectorLayer, QgsRasterLayer, QgsPointXY)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDialog

from mhm_qgis.qgis_bridge.layers.domain import snap_points_to_network, DomainWorkflow
from mhm_qgis.core.handlers.state import domain_state
from mhm_qgis.core.executions.morphology import delineation
from mhm_qgis.core.handlers.raster.tasks import dem_derivative_files, hydrology_files
from mhm_qgis.qt.controllers.domain_delineator import colour_outlets


def layer(kind, crs, field, features):
    result = QgsVectorLayer(f"{kind}?crs={crs}&field={field}:integer", kind, "memory")
    for value, wkt in features:
        feature = QgsFeature(result.fields())
        feature.setAttributes([value])
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        result.dataProvider().addFeatures([feature])
    return result


def snapping(folder):
    for crs, point, lines, radius, count, distance, order in (
        ("EPSG:32632", "POINT(0 0)", [(1, "LINESTRING(500 -10,500 10)")], 1000, 1, 500, 1),
        ("EPSG:32632", "POINT(0 0)", [(1, "LINESTRING(500 -10,500 10)"),
          (3, "LINESTRING(800 -10,800 10)")], 1000, 2, 800, 3),
        ("EPSG:32632", "POINT(0 0)", [(1, "LINESTRING(7000 -10,7000 10)")], 1000, 0, 7000, 1),
        ("EPSG:4326", "POINT(10 60)", [(2, "LINESTRING(10.01 59.99,10.01 60.01)")], 1000, 1, 558, 2),
        ("EPSG:4326", "POINT(10 0)", [(2, "LINESTRING(10.01 -0.01,10.01 0.01)")], 1000, 0, 1113, 2),
        ("EPSG:2263", "POINT(1000000 200000)", [(1, "LINESTRING(1001000 199990,1001000 200010)")], 400, 1, 304.8, 1),
        # Bounding-box-nearest is not geometry-nearest for this bent line.
        ("EPSG:32632", "POINT(0 0)", [(9, "LINESTRING(-5000 0,-5000 5000,0 5000)"),
          (1, "LINESTRING(2000 -10,2000 10)")], 100, 0, 2000, 1),
    ):
        points = layer("Point", crs, "id", [(1, point)])
        network = layer("LineString", crs, "Order", lines)
        path = snap_points_to_network(points, network, folder / "snapped.shp",
                                      project_crs=QgsCoordinateReferenceSystem(crs), max_snap_buffer_distance=radius)
        output = QgsVectorLayer(path, "snapped", "ogr")
        feature = next(output.getFeatures())
        assert feature["snap_count"] == count, feature.attributes()
        assert feature["confidence"] == (2 if count == 1 else int(count > 1)), feature.attributes()
        assert abs(feature["snap_dist"] - distance) < 2, feature.attributes()
        assert feature["snap_order"] == order
        output = None
    empty = layer("LineString", "EPSG:32632", "Order", [])
    path = snap_points_to_network(points, empty, folder / "empty.shp")
    result = QgsVectorLayer(path, "empty", "ogr")
    feature = next(result.getFeatures())
    assert feature["confidence"] == 0 and feature["snap_state"] == "failed"


def navigation(folder):
    import numpy as np
    from osgeo import gdal, osr
    from mhm_qgis.qt.dialogs.domain_delineator import DomainDelineatorDialog

    points = layer("Point", "EPSG:32632", "id", [(1, "POINT(850 150)"), (2, "POINT(950 50)")])
    workflow = DomainWorkflow(folder, points, "id", ["1", "2"])
    Path(workflow.filled_dem_path).parent.mkdir(parents=True, exist_ok=True)
    original = folder / "source.tif"
    dem = gdal.GetDriverByName("GTiff").Create(str(original), 10, 10, 1, gdal.GDT_Float32)
    dem.SetGeoTransform((0, 100, 0, 1000, 0, -100))
    crs = osr.SpatialReference(); crs.ImportFromEPSG(32632)
    dem.SetProjection(crs.ExportToWkt())
    dem.GetRasterBand(1).WriteArray(np.add.outer(np.arange(10, 0, -1), np.arange(10, 0, -1)))
    dem.GetRasterBand(1).SetNoDataValue(-9999)
    dem = None
    result = dem_derivative_files(original, Path(workflow.filled_dem_path).parent)
    for name in ("filled_dem", "slope", "aspect", "flow_accumulation", "flow_direction"):
        assert QgsRasterLayer(result[name], name).isValid(), name
    from mhm_qgis.qt.objects.viewport_raster_range import raster_window_range
    assert raster_window_range(workflow.flow_accumulation_path, (0, 0, 1000, 1000))[1] > 1
    hydrology_files(workflow.filled_dem_path, channel_path=workflow.channel_network_path, threshold_cells=1)
    main = QDialog()
    main.project_folder = str(folder)
    main.get_crs = lambda: QgsCoordinateReferenceSystem("EPSG:32632")
    main.log_message = Mock()
    main.selected_input_file_paths = lambda: []
    main.task_coordinator = Mock()
    main.save_input_state = Mock()
    main.update_gauged_outlet_count = Mock()

    def prepare(dialog, _context):
        dialog._filled_dem_layer = QgsRasterLayer(workflow.filled_dem_path, "DEM")
        dialog._flow_accumulation_layer = None
        dialog._channel_layer = None
        dialog._watershed_layer = None
        dialog.canvas.setDestinationCrs(main.get_crs())

    with patch.object(DomainDelineatorDialog, "_prepare_map_layers", prepare):
        dialog = DomainDelineatorDialog(main, points, "id", ["1", "2"], {})
        main.task_coordinator.submit.assert_not_called()
        assert all(record.get("confidence") in (0, 1, 2) for record in dialog._draft_state["outlets"].values())
        for confidence, colour in ((0, "#f5a3a3"), (1, "#ffe794"), (2, "#a8e6a3")):
            dialog._draft_state["outlets"]["1"]["confidence"] = confidence
            colour_outlets(dialog)
            assert dialog.listWidget_outlets.item(0).background().color().name() == colour
        dialog.checkBox_isDomainOutlet.setChecked(True)
        dialog.listWidget_outlets.setCurrentRow(1)
        assert dialog._draft_state["outlets"]["1"]["is_domain"]
        dialog.listWidget_outlets.setCurrentRow(0)
        dialog.pushButton_nextPourPoint.click()
        main.task_coordinator.submit.assert_not_called()
        dialog._point_picked(QgsPointXY(950, 50), Qt.MouseButton.LeftButton)
        main.task_coordinator.submit.assert_not_called()
        dialog.pushButton_showDelineation.click()
        call = main.task_coordinator.submit.call_args
        assert call.args[0] == "domain-watershed-preview"
        result = call.args[2](None)
        call.kwargs["on_success"](result)
        assert dialog._watershed_layer.isValid()
        assert not any(r.get("is_domain") for r in domain_state.load_state(folder)["outlets"].values())
        main.task_coordinator.submit.reset_mock()
        dialog.listWidget_outlets.setCurrentRow(0)
        dialog.listWidget_outlets.setCurrentRow(1)
        main.task_coordinator.submit.assert_not_called()
        assert dialog._watershed_layer.isValid()
        dialog.pushButton_save.click()
        call = main.task_coordinator.submit.call_args
        assert call.args[0] == "domain-final-save"
        saved = call.args[2](None)
        with patch("mhm_qgis.qt.controllers.domain_delineator.QMessageBox.information"):
            call.kwargs["on_success"](saved)
        state = domain_state.load_state(folder)
        assert len(domain_state.active_domain_records(state)) == 1
        assert all(record["mask_path"] for record in state["outlets"].values())
        assert state["merged_mask_path"]
        assert state["outlets"]["2"]["mask_path"].endswith(Path(result["raster_path"]).name)
        main.task_coordinator.submit.reset_mock()
        dialog._cleanup()

        # A new dialog can show the mask from state without starting any task.
        reopened = DomainDelineatorDialog(main, points, "id", ["1", "2"], {})
        reopened.listWidget_outlets.setCurrentRow(1)
        assert reopened._watershed_layer.isValid()
        main.task_coordinator.submit.assert_not_called()
        reopened._point_picked(QgsPointXY(850, 150), Qt.MouseButton.LeftButton)
        assert reopened._watershed_layer is None
        main.task_coordinator.submit.assert_not_called()
        reopened._cleanup()


if __name__ == "__main__":
    application = QgsApplication([], False)
    application.initQgis()
    with TemporaryDirectory() as temporary:
        snapping(Path(temporary))
        navigation(Path(temporary))
        from qgis.PyQt.QtCore import QCoreApplication, QEvent
        for widget in application.topLevelWidgets():
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.exitQgis()
    print("Real QGIS snapping and deferred navigation passed")
