"""Domain DEMs must share the common L0 grid with every other model input."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from osgeo import gdal, ogr, osr

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mhm_qgis import standalone  # noqa: E402

standalone.install(force=True)

from mhm_qgis.core.handlers.raster.tasks import write_domain_dem_ascii  # noqa: E402
from mhm_qgis.core.handlers.file.ascii.pad import read_ascii_header  # noqa: E402


L0 = {
    "ncols": 6, "nrows": 5, "xllcorner": 0.0, "yllcorner": 0.0,
    "cellsize": 100.0, "nodata_value": -9999.0,
}


def _cropped_dem(path):
    """A DEM already cropped to the common L0 extent."""
    dataset = gdal.GetDriverByName("GTiff").Create(
        str(path), L0["ncols"], L0["nrows"], 1, gdal.GDT_Float32
    )
    dataset.SetGeoTransform((0.0, 100.0, 0, 500.0, 0, -100.0))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(32632)
    dataset.SetProjection(crs.ExportToWkt())
    values = np.arange(
        L0["ncols"] * L0["nrows"], dtype="float32"
    ).reshape(L0["nrows"], L0["ncols"]) + 1
    dataset.GetRasterBand(1).WriteArray(values)
    dataset.GetRasterBand(1).SetNoDataValue(-9999)
    dataset = None
    return values


def _polygon(path, xmin, xmax, ymin, ymax):
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(32632)
    source = ogr.GetDriverByName("ESRI Shapefile").CreateDataSource(str(path))
    layer = source.CreateLayer("domain", crs, ogr.wkbPolygon)
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)):
        ring.AddPoint(x, y)
    polygon = ogr.Geometry(ogr.wkbPolygon)
    polygon.AddGeometry(ring)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(polygon)
    layer.CreateFeature(feature)
    source = None


def _rows(path):
    lines = path.read_text(encoding="utf-8").splitlines()[6:]
    return [line.split() for line in lines if line.strip()]


def test_the_domain_dem_keeps_the_common_extent_not_the_domain_bounding_box(tmp_path):
    """This is the point of the change: same matrix size for every domain."""
    dem = tmp_path / "1_dem_filled_crop.tif"
    polygon = tmp_path / "domain.shp"
    output = tmp_path / "data" / "outlet_1" / "dem.asc"
    values = _cropped_dem(dem)
    # x 200-400 covers column centres 250/350 (columns 2-3); with ymax 500,
    # y 200-400 covers row centres 350/250 (rows 1-2).
    _polygon(polygon, 200.0, 400.0, 200.0, 400.0)

    write_domain_dem_ascii(dem, output, L0, polygon, reference_path=dem)

    header = read_ascii_header(output)
    assert (int(header["ncols"]), int(header["nrows"])) == (6, 5)
    assert header["xllcorner"] == 0.0 and header["yllcorner"] == 0.0
    assert header["cellsize"] == 100.0

    rows = _rows(output)
    assert len(rows) == 5 and all(len(row) == 6 for row in rows)
    kept = np.zeros((5, 6), dtype=bool)
    kept[1:3, 2:4] = True
    written = np.array([[float(cell) for cell in row] for row in rows])
    np.testing.assert_array_equal(written[kept], values[kept])
    assert np.all(written[~kept] == -9999)


def test_two_domains_produce_identical_matrix_sizes(tmp_path):
    dem = tmp_path / "crop.tif"
    _cropped_dem(dem)
    headers = []
    for index, bounds in enumerate(
            ((0.0, 200.0, 300.0, 500.0), (400.0, 600.0, 0.0, 200.0))):
        polygon = tmp_path / f"d{index}.shp"
        _polygon(polygon, *bounds)
        output = tmp_path / f"d{index}" / "dem.asc"
        write_domain_dem_ascii(dem, output, L0, polygon, reference_path=dem)
        headers.append(read_ascii_header(output))
    # Different catchments, same grid -- which is what mHM needs.
    assert headers[0] == headers[1]


def test_a_domain_outside_the_extent_yields_an_all_nodata_grid(tmp_path):
    dem = tmp_path / "crop.tif"
    polygon = tmp_path / "far.shp"
    output = tmp_path / "dem.asc"
    _cropped_dem(dem)
    _polygon(polygon, 10000.0, 10200.0, 10000.0, 10200.0)

    write_domain_dem_ascii(dem, output, L0, polygon, reference_path=dem)

    rows = _rows(output)
    assert len(rows) == 5
    assert all(cell == "-9999" for row in rows for cell in row)


def test_a_failed_write_leaves_no_partial_file(tmp_path):
    dem = tmp_path / "crop.tif"
    output = tmp_path / "dem.asc"
    _cropped_dem(dem)

    with pytest.raises(RuntimeError, match="Could not open the mask layer"):
        write_domain_dem_ascii(dem, output, L0, tmp_path / "missing.shp")
    assert not output.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_source_nodata_becomes_the_ascii_nodata(tmp_path):
    dem = tmp_path / "crop.tif"
    polygon = tmp_path / "all.shp"
    output = tmp_path / "dem.asc"
    _cropped_dem(dem)
    # Punch a nodata hole in the source.
    dataset = gdal.Open(str(dem), gdal.GA_Update)
    band = dataset.GetRasterBand(1)
    values = band.ReadAsArray()
    values[0, 0] = -9999
    band.WriteArray(values)
    dataset = None
    _polygon(polygon, 0.0, 600.0, 0.0, 500.0)

    write_domain_dem_ascii(dem, output, L0, polygon, reference_path=dem)

    assert _rows(output)[0][0] == "-9999"


def _domain_state(project, outlets, dem_domain=False):
    from mhm_qgis.core.handlers.state.domain_state import save_state

    save_state(project, {
        "definition_mode": "domain_delineator",
        "pour_points_source": "outlets.shp",
        "outlet_id_field": "id",
        "outlet_order": list(outlets),
        "dem_domain": dem_domain,
        "outlets": {
            outlet: {"is_domain": True, "mask_path": f"masks/{outlet}.tif"}
            for outlet in outlets
        },
    })


def test_the_plan_lists_every_active_domain_with_its_mask(tmp_path):
    from mhm_qgis.core.morphology.layers.domain_dem import domain_dem_plan

    _domain_state(tmp_path, ("001", "002"), dem_domain=True)
    plan = domain_dem_plan(tmp_path)

    assert [entry["name"] for entry in plan] == ["001", "002", "dem_extent"]
    assert [entry["domain_id"] for entry in plan] == [1, 2, 3]
    # Each entry names where its DEM will be written, on the shared grid.
    for entry in plan:
        assert entry["dem_path"].endswith(f"data/{entry['name']}/dem.asc")
        assert entry["mask"]
    # The valid-DEM domain uses the delineator mask, not an outlet mask.
    assert plan[-1]["mask"].endswith("4_watershed_DEM.tif")


def test_writing_domain_dems_refuses_a_missing_mask(tmp_path):
    from mhm_qgis.core.morphology.layers.domain_dem import write_domain_dems

    dem = tmp_path / "crop.tif"
    _cropped_dem(dem)
    _domain_state(tmp_path, ("001",))

    with pytest.raises(FileNotFoundError, match="no delineated mask"):
        write_domain_dems(tmp_path, str(dem), L0)


def test_writing_domain_dems_refuses_a_missing_cropped_dem(tmp_path):
    from mhm_qgis.core.morphology.layers.domain_dem import write_domain_dems

    _domain_state(tmp_path, ("001",))
    with pytest.raises(FileNotFoundError, match="cropped L0 DEM is required"):
        write_domain_dems(tmp_path, str(tmp_path / "absent.tif"), L0)


def test_no_active_domains_is_a_no_op(tmp_path):
    from mhm_qgis.core.morphology.layers.domain_dem import write_domain_dems

    assert write_domain_dems(tmp_path, str(tmp_path / "absent.tif"), L0) == []


def test_the_state_records_the_domain_plan(tmp_path):
    from mhm_qgis.core.handlers.state import processing

    plan = [{
        "domain_id": 1, "outlet_id": "001", "name": "001",
        "mask": "/p/masks/001.tif", "directory": "/p/data/001",
        "dem_path": "/p/data/001/dem.asc",
    }]
    saved = processing.save_domains(tmp_path, plan)

    assert saved == plan
    assert processing.section(tmp_path, "domains") == plan


def _master_inputs(project, names):
    from mhm_qgis.core.handlers.store.layout import morph_folder

    master = Path(morph_folder(project))
    master.mkdir(parents=True, exist_ok=True)
    for name in names:
        (master / name).write_text(f"content of {name}\n", encoding="utf-8")
    return master


def test_the_shared_morphology_inputs_are_linked_into_the_domain(tmp_path):
    from mhm_qgis.core.morphology.layers.domain_dem import link_master_inputs

    _master_inputs(tmp_path, (
        "slope.asc", "slope.prj", "aspect.asc", "facc.asc", "fdir.asc",
        "soil_class.asc", "soil_classdefinition.txt",
        "geology_class.asc", "geology_classdefinition.txt",
        "idgauges.asc", "idgauges.prj",
        # Not requested for domains, so it must stay behind.
        "lc_2015_2015.asc", "dem.asc",
    ))
    domain = tmp_path / "data" / "280"

    linked = link_master_inputs(tmp_path, domain)

    names = sorted(path.name for path in linked)
    assert names == [
        "aspect.asc", "facc.asc", "fdir.asc", "geology_class.asc",
        "geology_classdefinition.txt", "idgauges.asc", "idgauges.prj",
        "slope.asc", "slope.prj",
        "soil_class.asc", "soil_classdefinition.txt",
    ]
    # Every shared input is a link back to data/master, not a second copy.
    assert all((domain / name).is_symlink() for name in names)
    # Class definitions travel with their raster.
    assert (domain / "soil_classdefinition.txt").read_text(
        encoding="utf-8") == "content of soil_classdefinition.txt\n"
    # The domain keeps its own dem.asc; master's is not linked over it.
    assert not (domain / "dem.asc").exists()
    assert not (domain / "lc_2015_2015.asc").exists()
    assert (domain / "idgauges.asc").read_text(encoding="utf-8") == "content of idgauges.asc\n"


def test_relinking_is_idempotent(tmp_path):
    from mhm_qgis.core.morphology.layers.domain_dem import link_master_inputs

    _master_inputs(tmp_path, ("slope.asc",))
    domain = tmp_path / "data" / "280"

    assert [p.name for p in link_master_inputs(tmp_path, domain)] == ["slope.asc"]
    assert [p.name for p in link_master_inputs(tmp_path, domain)] == ["slope.asc"]
    assert (domain / "slope.asc").is_symlink()


def test_a_regenerated_master_file_needs_no_relink(tmp_path):
    from mhm_qgis.core.morphology.layers.domain_dem import link_master_inputs

    master = _master_inputs(tmp_path, ("slope.asc",))
    domain = tmp_path / "data" / "280"
    link_master_inputs(tmp_path, domain)

    (master / "slope.asc").write_text("regenerated slope\n", encoding="utf-8")

    # The link resolves to master, so the domain follows it with no second pass.
    assert (domain / "slope.asc").read_text(encoding="utf-8") == "regenerated slope\n"


def test_a_stale_copy_from_an_older_run_is_replaced_by_a_link(tmp_path):
    from mhm_qgis.core.morphology.layers.domain_dem import link_master_inputs

    _master_inputs(tmp_path, ("slope.asc",))
    domain = tmp_path / "data" / "280"
    domain.mkdir(parents=True, exist_ok=True)
    (domain / "slope.asc").write_text("stale copy\n", encoding="utf-8")

    link_master_inputs(tmp_path, domain)

    assert (domain / "slope.asc").is_symlink()
    assert (domain / "slope.asc").read_text(
        encoding="utf-8") == "content of slope.asc\n"


def test_v6_soil_names_are_linked_when_present(tmp_path):
    from mhm_qgis.core.morphology.layers.domain_dem import link_master_inputs

    _master_inputs(tmp_path, (
        "soil_horizon_class.nc", "soil_classdefinition_iFlag_soilDB_1.txt",
    ))
    linked = link_master_inputs(tmp_path, tmp_path / "data" / "280")

    assert sorted(path.name for path in linked) == [
        "soil_classdefinition_iFlag_soilDB_1.txt", "soil_horizon_class.nc",
    ]


def test_missing_master_files_are_simply_skipped(tmp_path):
    from mhm_qgis.core.morphology.layers.domain_dem import link_master_inputs

    assert link_master_inputs(tmp_path, tmp_path / "data" / "280") == []


def test_what_master_could_not_supply_is_logged(tmp_path):
    """A silent skip once left every domain holding only its dem.asc."""
    from mhm_qgis.core.morphology.layers.domain_dem import link_master_inputs

    _master_inputs(tmp_path, ("slope.asc",))
    messages = []

    link_master_inputs(tmp_path, tmp_path / "data" / "280", log=messages.append)

    reported = "\n".join(messages)
    assert "soil class" in reported
    assert "geology class" in reported


def test_one_spelling_of_a_group_is_enough_to_stay_quiet(tmp_path):
    """v5 soil files must not be reported as missing just because v6's are."""
    from mhm_qgis.core.morphology.layers.domain_dem import link_master_inputs

    _master_inputs(tmp_path, (
        "slope.asc", "aspect.asc", "facc.asc", "fdir.asc",
        "soil_class.asc", "soil_classdefinition.txt",
        "geology_class.asc", "geology_classdefinition.txt",
    ))
    messages = []

    link_master_inputs(tmp_path, tmp_path / "data" / "280", log=messages.append, strict=True)

    assert not [line for line in messages if line.startswith("WARNING")]


def test_a_published_advanced_soil_raster_reaches_the_domain(tmp_path):
    """Publish runs first, so its output is in data/master by the link step.

    An advanced or mHM-ready soil raster sits in the staging folder until
    `publish_model_inputs` moves it, so linking before that step silently gave
    every domain a bare dem.asc.
    """
    from mhm_qgis.core.morphology.layers.advanced_l0 import publish_model_inputs
    from mhm_qgis.core.morphology.layers.domain_dem import link_master_inputs
    from mhm_qgis.core.handlers.store.paths import morph_staging_folder

    staging = Path(morph_staging_folder(tmp_path))
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "soil_class.asc").write_text(
        "ncols         2\nnrows         2\nxllcorner     100.0\n"
        "yllcorner     100.0\ncellsize      100.0\nNODATA_value  -9999\n"
        "1 2\n3 4\n",
        encoding="utf-8",
    )
    (staging / "soil_classdefinition.txt").write_text("1 sand\n", encoding="utf-8")
    domain = tmp_path / "data" / "280"
    target = {
        "ncols": 4, "nrows": 4, "xllcorner": 0.0, "yllcorner": 0.0,
        "cellsize": 100.0, "unit": "m", "nodata_value": -9999.0,
    }

    # Nothing has been published yet, so nothing can be linked.
    assert link_master_inputs(tmp_path, domain) == []

    publish_model_inputs(tmp_path, target)
    linked = link_master_inputs(tmp_path, domain)

    assert sorted(path.name for path in linked) == [
        "soil_class.asc", "soil_classdefinition.txt",
    ]
    # The domain reaches the raster that was padded onto the common L0 grid.
    assert read_ascii_header(domain / "soil_class.asc")["ncols"] == 4
    assert (domain / "soil_classdefinition.txt").read_text(
        encoding="utf-8") == "1 sand\n"
