"""QGIS-free raster and watershed jobs suitable for ``QgsTask`` workers."""
from __future__ import annotations

import math
import os
from pathlib import Path

from ....externals import pyflwdir_handler
from ...grid import (
    aligned_window,
    geotransform_from_header,
    header_bounds,
    header_from_geotransform,
)


def _dependencies():
    deps = pyflwdir_handler.dependencies()
    return (
        deps["np"], deps["pfd"], deps["Affine"],
        deps["gdal"], deps["ogr"], deps["osr"],
    )


def _cancelled(task):
    if task is not None and task.isCanceled():
        raise RuntimeError("Task cancelled.")


def _progress(task, value):
    if task is not None:
        task.setProgress(float(value))


class TaskShare:
    """A task seen by one step of a longer job: its 0-100 % fills `start`-`end`.

    Several steps each reporting 0-100 % would otherwise restart the bar.
    """

    def __init__(self, task, start=0.0, end=100.0):
        self.task, self.start, self.end = task, float(start), float(end)

    def isCanceled(self):
        return self.task is not None and self.task.isCanceled()

    def setProgress(self, value):
        if self.task is not None:
            self.task.setProgress(self.start + (self.end - self.start) * float(value) / 100.0)

    def part(self, index, count):
        """Return the `index`-th of `count` equal consecutive shares of this one."""
        span = (self.end - self.start) / max(1, count)
        return TaskShare(self.task, self.start + span * index, self.start + span * (index + 1))


def _read_raster(path, *, as_float=False):
    import numpy as np
    from osgeo import gdal

    dataset = gdal.Open(str(path))
    if dataset is None:
        raise RuntimeError(f"Could not open raster: {path}")
    band = dataset.GetRasterBand(1)
    array = band.ReadAsArray()
    if as_float:
        array = array.astype(np.float32)
    result = {
        "array": array,
        "nodata": band.GetNoDataValue(),
        "geotransform": dataset.GetGeoTransform(),
        "projection": dataset.GetProjection(),
        "rows": dataset.RasterYSize,
        "cols": dataset.RasterXSize,
    }
    band = None
    dataset = None
    return result


def _write_raster(path, array, reference, nodata, data_type):
    if Path(path).suffix == ".nc":
        from ..file.netcdf.raster import write_array

        return write_array(path, Path(path).stem, array, reference, nodata)
    from osgeo import gdal

    path = os.path.abspath(str(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    dataset = gdal.GetDriverByName("GTiff").Create(
        path,
        int(reference["cols"]),
        int(reference["rows"]),
        1,
        data_type,
        options=["COMPRESS=LZW"],
    )
    if dataset is None:
        raise RuntimeError(f"Could not create raster: {path}")
    dataset.SetGeoTransform(reference["geotransform"])
    if reference.get("projection"):
        dataset.SetProjection(reference["projection"])
    band = dataset.GetRasterBand(1)
    band.SetNoDataValue(float(nodata))
    band.WriteArray(array)
    band.FlushCache()
    band = None
    dataset = None
    return path


def _aligned_l0_window(source, source_path, target_header, reference_path=None):
    """Validate an L0 raster against the target header and return its copy window."""
    _np, _pfd, _affine, gdal, _ogr, osr = _dependencies()
    try:
        source_header = header_from_geotransform(
            source.GetGeoTransform(), source.RasterXSize, source.RasterYSize
        )
    except ValueError as error:
        raise ValueError(
            f"L0 raster is not on the filled-DEM resolution: {source_path}"
        ) from error
    window = aligned_window(source_header, target_header, label=f"L0 raster {source_path}")

    if reference_path:
        reference = gdal.Open(str(reference_path), gdal.GA_ReadOnly)
        if reference is None:
            raise RuntimeError(f"Could not open L0 reference raster: {reference_path}")
        reference_header = header_from_geotransform(
            reference.GetGeoTransform(), reference.RasterXSize, reference.RasterYSize
        )
        aligned_window(source_header, reference_header, label=f"L0 raster {source_path}")
        source_srs = osr.SpatialReference(wkt=source.GetProjection())
        reference_srs = osr.SpatialReference(wkt=reference.GetProjection())
        if source.GetProjection() and reference.GetProjection() and not source_srs.IsSame(reference_srs):
            raise ValueError(f"L0 raster CRS differs from the filled DEM: {source_path}")
        reference = None
    return window


def _copy_aligned_l0_raster(
        source_path,
        output_path,
        target_header,
        reference_path=None,
        mask=None,
        pad_value=None,
        task=None):
    """Window-copy an aligned L0 raster, padding it and optionally masking it.

    ``pad_value`` is written to the cells the source window does not reach and
    defaults to the band nodata. Categorical layers pass a valid class instead,
    so expanding a layer to the common extent never leaves nodata inside the
    model domain. Masked cells always keep the band nodata.
    """
    np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    source = gdal.Open(str(source_path), gdal.GA_ReadOnly)
    if source is None:
        raise RuntimeError(f"Could not open L0 raster: {source_path}")
    window = _aligned_l0_window(source, source_path, target_header, reference_path)
    cellsize = window["cellsize"]
    target_xmin = window["target_xmin"]
    target_ymax = window["target_ymax"]
    target_cols = window["target_cols"]
    target_rows = window["target_rows"]
    source_x = window["source_x"]
    source_y = window["source_y"]
    target_x = window["target_x"]
    target_y = window["target_y"]
    copy_cols = window["copy_cols"]
    copy_rows = window["copy_rows"]

    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.tmp{output.suffix or '.tif'}")
    temporary.unlink(missing_ok=True)
    data_type = source.GetRasterBand(1).DataType
    destination = gdal.GetDriverByName("GTiff").Create(
        str(temporary),
        target_cols,
        target_rows,
        source.RasterCount,
        data_type,
        options=["TILED=YES", "COMPRESS=LZW", "SPARSE_OK=TRUE"],
    )
    if destination is None:
        raise RuntimeError(f"Could not create cropped L0 raster: {output_path}")
    destination.SetGeoTransform(
        (target_xmin, cellsize, 0.0, target_ymax, 0.0, -cellsize)
    )
    destination.SetProjection(source.GetProjection())

    try:
        for band_index in range(1, source.RasterCount + 1):
            _cancelled(task)
            source_band = source.GetRasterBand(band_index)
            target_band = destination.GetRasterBand(band_index)
            nodata = source_band.GetNoDataValue()
            if nodata is None:
                nodata = float(target_header.get("nodata_value", -9999.0))
            target_band.SetNoDataValue(float(nodata))
            target_band.Fill(float(nodata if pad_value is None else pad_value))
            if copy_cols > 0 and copy_rows > 0:
                block_rows = min(512, copy_rows)
                for row in range(0, copy_rows, block_rows):
                    _cancelled(task)
                    height = min(block_rows, copy_rows - row)
                    values = source_band.ReadAsArray(
                        source_x, source_y + row, copy_cols, height
                    )
                    if values is None:
                        raise RuntimeError(f"Could not read L0 raster window: {source_path}")
                    if mask is not None:
                        keep = mask[
                            target_y + row: target_y + row + height,
                            target_x: target_x + copy_cols,
                        ]
                        values = np.where(keep, values, nodata).astype(values.dtype)
                    target_band.WriteArray(values, target_x, target_y + row)
            target_band.FlushCache()
            _progress(task, 100.0 * band_index / source.RasterCount)
        destination.FlushCache()
    except Exception:
        destination = None
        source = None
        temporary.unlink(missing_ok=True)
        raise
    destination = None
    source = None
    os.replace(temporary, output)
    return str(output)


def crop_aligned_l0_raster(
        source_path,
        output_path,
        target_header,
        *,
        reference_path=None,
        pad_value=None,
        task=None):
    """Window-copy an aligned L0 raster and pad outside its extent.

    Cells beyond the source extent take ``pad_value``, or the band nodata when
    no pad value is given.
    """
    return _copy_aligned_l0_raster(
        source_path,
        output_path,
        target_header,
        reference_path=reference_path,
        pad_value=pad_value,
        task=task,
    )


def warp_raster_to_header(
    source_path,
    output_path,
    target_header,
    target_crs,
    *,
    source_crs="",
    resampling="near",
    pad_value=None,
    task=None,
):
    """Reproject one raster directly onto the exact common-L0 grid."""
    _np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.warp{output.suffix}")
    temporary.unlink(missing_ok=True)
    xmin, xmax, ymin, ymax = header_bounds(target_header)
    driver = {
        ".asc": "AAIGrid",
        ".nc": "netCDF",
        ".tif": "GTiff",
        ".tiff": "GTiff",
    }.get(output.suffix.lower())
    if driver is None:
        raise ValueError(f"Unsupported ready-raster output: {output}")

    def progress(fraction, _message, _data):
        if task is not None and task.isCanceled():
            return 0
        _progress(task, fraction * 100)
        return 1

    options = {
        "format": driver,
        "outputBounds": (xmin, ymin, xmax, ymax),
        "width": int(target_header["ncols"]),
        "height": int(target_header["nrows"]),
        "dstSRS": str(target_crs),
        "resampleAlg": resampling,
        "multithread": True,
        "callback": progress,
    }
    if source_crs:
        options["srcSRS"] = str(source_crs)
    if pad_value is not None:
        options.update(dstNodata=float(pad_value), warpOptions=["INIT_DEST=NO_DATA"])
    result = gdal.Warp(str(temporary), str(source_path), **options)
    if result is None:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Could not place raster on the common grid: {source_path}")
    result = None
    os.replace(temporary, output)
    temporary_projection = temporary.with_suffix(".prj")
    if temporary_projection.is_file():
        os.replace(temporary_projection, output.with_suffix(".prj"))
    return str(output)


def _rasterize_target_mask(vector_path, target_header, projection):
    """Return a boolean mask of the target grid cells covered by a polygon layer."""
    _np, _pfd, _affine, gdal, ogr, _osr = _dependencies()
    cols = int(target_header["ncols"])
    rows = int(target_header["nrows"])
    dataset = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Byte)
    if dataset is None:
        raise RuntimeError("Could not allocate the L0 mask grid.")
    dataset.SetGeoTransform(geotransform_from_header(target_header))
    if projection:
        dataset.SetProjection(projection)
    source = ogr.Open(str(vector_path))
    if source is None:
        raise RuntimeError(f"Could not open the mask layer: {vector_path}")
    status = gdal.RasterizeLayer(dataset, [1], source.GetLayer(0), burn_values=[1])
    source = None
    if status != 0:
        dataset = None
        raise RuntimeError(f"Could not rasterize the mask layer: {vector_path}")
    values = dataset.GetRasterBand(1).ReadAsArray().astype(bool)
    dataset = None
    return values


def _raster_target_mask(mask_path, target_header, reference_path=None):
    """Return a boolean target-grid mask from an aligned delineation raster.

    The mask rasters written by :func:`_delineate` sit on the filled-DEM grid
    with 0 outside the basin, so the target mask is a window copy rather than a
    rasterization.
    """
    np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    source = gdal.Open(str(mask_path), gdal.GA_ReadOnly)
    if source is None:
        raise RuntimeError(f"Could not open the mask raster: {mask_path}")
    try:
        window = _aligned_l0_window(
            source, mask_path, target_header, reference_path
        )
        keep = np.zeros(
            (window["target_rows"], window["target_cols"]), dtype=bool
        )
        if window["copy_cols"] > 0 and window["copy_rows"] > 0:
            values = source.GetRasterBand(1).ReadAsArray(
                window["source_x"],
                window["source_y"],
                window["copy_cols"],
                window["copy_rows"],
            )
            if values is None:
                raise RuntimeError(f"Could not read the mask raster: {mask_path}")
            keep[
                window["target_y"]: window["target_y"] + window["copy_rows"],
                window["target_x"]: window["target_x"] + window["copy_cols"],
            ] = values != 0
    finally:
        source = None
    return keep


def _target_mask(mask_path, target_header, projection, reference_path=None):
    """Return the target-grid mask for a delineation raster or a polygon layer.

    Domains are delineated as pyflwdir masks, so no polygon is written for them.
    The legacy watershed flow still produces a merged shapefile, which is why a
    vector mask stays supported.
    """
    _np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    try:
        dataset = gdal.OpenEx(str(mask_path), gdal.OF_RASTER)
    finally:
        gdal.PopErrorHandler()
    if dataset is None:
        return _rasterize_target_mask(mask_path, target_header, projection)
    dataset = None
    return _raster_target_mask(mask_path, target_header, reference_path)


def mask_aligned_l0_raster(
        source_path,
        output_path,
        target_header,
        mask_path,
        *,
        reference_path=None,
        pad_value=None,
        task=None):
    """Apply a domain mask to an aligned L0 raster without resampling it.

    ``mask_path`` is a delineation mask raster or a polygon layer. Cells outside
    the mask become nodata; cells beyond the source extent take ``pad_value`` as
    in :func:`crop_aligned_l0_raster`.
    """
    _np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    source = gdal.Open(str(source_path), gdal.GA_ReadOnly)
    if source is None:
        raise RuntimeError(f"Could not open L0 raster: {source_path}")
    projection = source.GetProjection()
    source = None
    return _copy_aligned_l0_raster(
        source_path,
        output_path,
        target_header,
        reference_path=reference_path,
        mask=_target_mask(
            mask_path, target_header, projection, reference_path
        ),
        pad_value=pad_value,
        task=task,
    )


ASCII_FORMAT = "ascii-grid-1"
_ASCII_BLOCK_ROWS = 64


def write_domain_dem_ascii(
        source_path,
        output_path,
        target_header,
        mask_path,
        *,
        reference_path=None,
        nodata=-9999,
        fill_value=None,
        band_index=1,
        task=None,
        project_folder=None):
    """Write one domain DEM as Arc/Info ASCII on the common L0 grid.

    The source is the cropped L0 DEM, so every domain shares the model extent and
    matches the other inputs cell for cell; only the cells inside the domain
    mask keep their value. Rows stream to text a block at a time, so memory stays
    flat whatever the grid size. With `project_folder`, an output whose inputs
    and options are unchanged since it was written is kept as it is.
    """
    output = Path(output_path)
    digest = None
    if project_folder:
        from ..file.netcdf.raster import file_path
        from ..state.cache import cached_payload, fingerprint, outputs_present

        digest = fingerprint(
            [file_path(source_path), *(str(path) for path in (mask_path, reference_path) if path)],
            {"source": str(source_path), "header": dict(target_header), "nodata": nodata,
             "fill_value": fill_value, "band": band_index, "format": ASCII_FORMAT},
        )
        # A cloned domain's output is a link into its source domain: rewrite it.
        if not output.is_symlink() and outputs_present(
                cached_payload(project_folder, "exports", str(output), digest)):
            _progress(task, 100)
            return str(output)

    np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    source = gdal.Open(str(source_path), gdal.GA_ReadOnly)
    if source is None:
        raise RuntimeError(f"Could not open the cropped L0 DEM: {source_path}")
    projection = source.GetProjection()
    window = _aligned_l0_window(source, source_path, target_header, reference_path)
    band = source.GetRasterBand(int(band_index))
    if band is None:
        source = None
        raise ValueError(
            f"Raster band {band_index} does not exist: {source_path}"
        )
    keep = _target_mask(mask_path, target_header, projection, reference_path) if mask_path else None

    cellsize = float(target_header["cellsize"])
    rows = int(target_header["nrows"])
    columns = int(target_header["ncols"])
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.unlink(missing_ok=True)

    blank = np.full(columns, float(nodata if fill_value is None else fill_value), dtype="float64")
    source_nodata = band.GetNoDataValue()
    copy_first = window["target_y"]
    copy_last = window["target_y"] + window["copy_rows"] if window["copy_rows"] > 0 else copy_first
    try:
        with open(temporary, "w", encoding="utf-8") as stream:
            stream.write(
                f"ncols         {columns}\n"
                f"nrows         {rows}\n"
                f"xllcorner     {float(target_header['xllcorner'])}\n"
                f"yllcorner     {float(target_header['yllcorner'])}\n"
                f"cellsize      {cellsize}\n"
                f"NODATA_value  {nodata}\n"
            )
            for start in range(0, rows, _ASCII_BLOCK_ROWS):
                _cancelled(task)
                stop = min(rows, start + _ASCII_BLOCK_ROWS)
                values = np.tile(blank, (stop - start, 1))
                first, last = max(start, copy_first), min(stop, copy_last)
                if first < last:
                    read = band.ReadAsArray(
                        window["source_x"], first - copy_first + window["source_y"],
                        window["copy_cols"], last - first)
                    if read is None:
                        raise RuntimeError(
                            f"Could not read the L0 DEM rows {first}-{last - 1}: {source_path}")
                    copied = values[first - start:last - start]
                    copied[:, window["target_x"]:window["target_x"] + window["copy_cols"]] = read
                    if source_nodata is not None:
                        copied[copied == source_nodata] = float(nodata)
                if fill_value is not None:
                    values = np.where(np.isfinite(values) & (values != nodata), values, fill_value)
                else:
                    values = np.where(np.isfinite(values), values, float(nodata))
                if keep is not None:
                    values = np.where(keep[start:stop], values, float(nodata))
                stream.writelines(f"{line}\n" for line in _ascii_rows(values))
                _progress(task, 100.0 * stop / rows)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        band = None
        source = None
    os.replace(temporary, output)
    if projection:
        try:
            temporary.write_text(projection, encoding="utf-8")
            os.replace(temporary, output.with_suffix(".prj"))
        finally:
            temporary.unlink(missing_ok=True)
    if digest:
        from ..state.cache import store_payload

        store_payload(project_folder, "exports", str(output), digest, {"outputs": [str(output)]})
    return str(output)


def _ascii_value(value) -> str:
    """Format one ASCII grid cell, keeping integers integral."""
    if value == int(value):
        return str(int(value))
    return repr(float(value))


def _ascii_rows(block) -> list[str]:
    """Format every row of `block` exactly as `_ascii_value` would.

    Turning a double into its shortest decimal text costs about half a
    microsecond per cell whatever does it (numpy's is slower than `repr`), so
    only rows of whole numbers -- most of a DEM, every class grid -- go faster,
    through int64.
    """
    import numpy as np

    whole = ((block == np.trunc(block)) & (np.abs(block) < 2.0 ** 63)).all(axis=1)
    return [
        " ".join(map(str, row.astype(np.int64).tolist())) if row_is_whole
        else " ".join([str(int(value)) if value == int(value) else repr(value) for value in row.tolist()])
        for row, row_is_whole in zip(block, whole.tolist(), strict=True)
    ]


def _prepared_dem_source(source, source_crs, target_crs, reprojected_path):
    _np, _pfd, _affine, gdal, _ogr, osr = _dependencies()
    source = str(source)
    if "|" in source and os.path.isfile(source.split("|", 1)[0]):
        source = source.split("|", 1)[0]
    selected_netcdf_variable = source.startswith('NETCDF:"')
    if not selected_netcdf_variable and (
        not target_crs or source_crs == target_crs
    ):
        return source
    source_ref = osr.SpatialReference()
    target_ref = osr.SpatialReference()
    source_valid = source_ref.SetFromUserInput(str(source_crs or "")) == 0
    target_valid = target_ref.SetFromUserInput(str(target_crs or "")) == 0
    reproject = source_valid and target_valid and not source_ref.IsSame(target_ref)
    if not selected_netcdf_variable and not reproject:
        return source
    os.makedirs(os.path.dirname(reprojected_path), exist_ok=True)
    if Path(reprojected_path).is_symlink():
        Path(reprojected_path).unlink()
    if reproject:
        result = gdal.Warp(
            str(reprojected_path), source, srcSRS=str(source_crs),
            dstSRS=str(target_crs), resampleAlg="near",
            multithread=True, format="netCDF",
        )
    else:
        options = {"format": "netCDF"}
        output_crs = target_crs or source_crs
        if output_crs:
            options["outputSRS"] = str(output_crs)
        result = gdal.Translate(str(reprojected_path), source, **options)
    if result is None:
        raise RuntimeError("DEM preparation failed.")
    result = None
    return str(reprojected_path)


DEM_DERIVATIVE_VARIABLES = {
    "slope": "slope", "aspect": "aspect",
    "flow_accumulation": "facc", "flow_direction": "fdir",
}


def dem_derivative_files(
    source, output_folder, *, source_crs="", target_crs="",
    reprojected_path="", compute=None, log=None, task=None, header=None,
):
    """Prepare one derivative dataset and a single-variable filled DEM."""
    from netCDF4 import Dataset

    from ..file.netcdf.raster import copy_rasters, file_path
    from ..file.netcdf.raster import source as raster_source
    from ..state.cache import fingerprint

    _cancelled(task)
    folder = Path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    bundle = folder / "dem_derivatives.nc"
    filled = folder / "filled_dem.nc"
    result = {
        "filled_dem": str(filled), "dem_derivatives": str(bundle),
        **{key: raster_source(bundle, var) for key, var in DEM_DERIVATIVE_VARIABLES.items()},
    }
    digest = fingerprint((file_path(source),), {
        "source": str(source), "source_crs": source_crs, "target_crs": target_crs,
        "format": "dem-netcdf-2", "header": header,
    })
    try:
        with Dataset(bundle) as dataset:
            reusable = (
                getattr(dataset, "input_fingerprint", "") == digest
                and {"dem", "dem_filled", *DEM_DERIVATIVE_VARIABLES.values()} <= set(dataset.variables)
            )
    except (OSError, RuntimeError):
        reusable = False
    if reusable:
        try:
            with Dataset(filled) as dataset:
                filled_valid = getattr(dataset, "input_fingerprint", "") == digest and "dem" in dataset.variables
        except (OSError, RuntimeError):
            filled_valid = False
        if filled_valid:
            return {**result, "reused": True}
    else:
        from tempfile import TemporaryDirectory

        prepared = _prepared_dem_source(
            source, source_crs, target_crs,
            reprojected_path or str(folder / "dem_reprojected.nc"),
        )
        with TemporaryDirectory(prefix="dem_derivatives_", dir=folder) as staging:
            written = (compute or _compute_dem_derivatives)(prepared, staging, log)
            _cancelled(task)
            with Dataset(written, "r+") as dataset:
                if not {"dem", "dem_filled", *DEM_DERIVATIVE_VARIABLES.values()} <= set(dataset.variables):
                    raise ValueError("DEM derivative NetCDF is missing required variables.")
                dataset.input_fingerprint = digest
            if header:
                # Derive only on real terrain, then pad all variables once.
                # Padding cannot create sinks or flow paths outside the DEM.
                names = ("dem", "dem_filled", *DEM_DERIVATIVE_VARIABLES.values())
                copy_rasters(bundle, {name: raster_source(written, name) for name in names}, header, task=task)
                with Dataset(bundle, "r+") as dataset:
                    dataset.input_fingerprint = digest
            else:
                os.replace(written, bundle)
    copy_rasters(filled, {"dem": raster_source(bundle, "dem_filled")}, task=task)
    with Dataset(filled, "r+") as dataset:
        dataset.input_fingerprint = digest
    _progress(task, 100)
    return {**result, "reused": False}


def _compute_dem_derivatives(prepared, staging, log):
    from ....externals.mhm_tools_handler import create_dem_derivative_files

    return create_dem_derivative_files(prepared, staging, log=log)


def _flow_context(filled_dem):
    """Reuse canonical D8 directions; allow a plain DEM for standalone callers."""
    from ..file.netcdf.raster import source

    bundle = Path(filled_dem).with_name("dem_derivatives.nc")
    if Path(filled_dem).name == "filled_dem.nc" and bundle.is_file():
        return pyflwdir_handler.flwdir_context(_read_raster(source(bundle, "fdir")), is_direction=True)
    return pyflwdir_handler.flwdir_context(_read_raster(filled_dem, as_float=True))


def hydrology_files(
    filled_dem,
    *,
    accumulation=False,
    area_path="",
    direction=False,
    channel_path="",
    threshold_cells=None,
    task=None,
):
    """Reuse bundled flow variables; compute only channel and area products."""
    from ..file.netcdf.raster import source

    bundle = Path(filled_dem).with_name("dem_derivatives.nc")
    result = {}
    for requested, key, variable in ((accumulation, "flow_accumulation", "facc"), (direction, "flow_direction", "fdir")):
        if requested:
            if not bundle.is_file():
                raise FileNotFoundError("Prepare DEM derivatives first.")
            result[key] = source(bundle, variable)
    if not area_path and not channel_path:
        return result
    _np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    _cancelled(task)
    context = _flow_context(filled_dem)
    reference = context["reference"]
    _progress(task, 20)
    cells = None
    if channel_path:
        # Raw, unrounded and unstamped: the channel threshold compares against
        # these, whereas the written raster uses `upstream_cells`, which rounds
        # to int32 and stamps nodata.
        cells = context["flwdir"].upstream_area(unit="cell")
    if area_path:
        values = pyflwdir_handler.upstream_area_m2(context)
        result["flow_accumulation_area"] = _write_raster(
            area_path, values, reference, -9999.0, gdal.GDT_Float32
        )
    _cancelled(task)
    _progress(task, 55)
    if channel_path:
        result["channel_network"] = _write_channel_network(
            channel_path, cells, context, threshold_cells
        )
    _progress(task, 100)
    return result




def gauge_position_file(filled_dem, gauges, output_path, *, task=None):
    """Burn the gauge locations already snapped by Domain Delineator."""
    import numpy as np
    from affine import Affine
    from osgeo import gdal

    _cancelled(task)
    reference = _read_raster(filled_dem)
    output = np.full(
        (reference["rows"], reference["cols"]),
        -9999,
        dtype=np.int32,
    )
    inverse = ~Affine.from_gdal(*reference["geotransform"])
    target_crs = reference.get("projection") or ""
    occupied = set()
    for index, gauge in enumerate(gauges):
        _cancelled(task)
        station_id, x, y, *source_crs = gauge
        if source_crs and source_crs[0] and target_crs:
            from pyproj import Transformer

            x, y = Transformer.from_crs(
                source_crs[0], target_crs, always_xy=True
            ).transform(float(x), float(y))
        col_value, row_value = inverse * (float(x), float(y))
        row, col = int(np.floor(row_value)), int(np.floor(col_value))
        if not (0 <= row < output.shape[0] and 0 <= col < output.shape[1]):
            raise ValueError(f"Gauge {station_id} lies outside the common L0 grid.")
        nodata = reference.get("nodata")
        value = reference["array"][row, col]
        if not np.isfinite(value) or (nodata is not None and value == nodata):
            raise ValueError(f"Gauge {station_id} lies on an invalid DEM cell.")
        if (row, col) in occupied:
            raise ValueError("Two gauges resolve to the same common-L0 cell.")
        occupied.add((row, col))
        output[row, col] = int(station_id)
        _progress(task, 15 + 80 * (index + 1) / max(1, len(gauges)))
    written = _write_raster(
        output_path,
        output,
        reference,
        -9999,
        gdal.GDT_Int32,
    )
    _progress(task, 100)
    return written


def _remove_vector(path, ogr):
    if os.path.exists(path):
        ogr.GetDriverByName("ESRI Shapefile").DeleteDataSource(str(path))


def _write_channel_network(path, accumulation, context, threshold_cells):
    np, _pfd, _affine, _gdal, ogr, osr = _dependencies()
    valid = accumulation[accumulation > 0]
    if valid.size == 0:
        raise RuntimeError("No valid flow-accumulation cells were found.")
    maximum = int(np.nanmax(valid))
    if threshold_cells is None:
        threshold = max(
            1,
            int(round(10_000_000.0 / pyflwdir_handler.cell_area_m2(context["reference"]))),
        )
        if threshold > maximum:
            threshold = max(1, int(np.nanpercentile(valid, 95)))
    else:
        threshold = int(threshold_cells)
        if threshold < 1 or threshold > maximum:
            raise ValueError(
                f"Channel threshold must be between 1 and {maximum} cells."
            )
    stream_mask = accumulation >= threshold
    order = context["flwdir"].stream_order("strahler", mask=stream_mask)
    streams = context["flwdir"].streams(
        mask=stream_mask, strord=order, uparea=accumulation
    )
    if not streams:
        raise RuntimeError("pyflwdir did not produce any stream features.")
    path = os.path.abspath(str(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _remove_vector(path, ogr)
    dataset = ogr.GetDriverByName("ESRI Shapefile").CreateDataSource(path)
    spatial_ref = osr.SpatialReference()
    srs = None
    if context["reference"].get("projection"):
        spatial_ref.ImportFromWkt(context["reference"]["projection"])
        srs = spatial_ref
    layer = dataset.CreateLayer(Path(path).stem, srs, ogr.wkbLineString)
    for name, field_type in (
        ("Order", ogr.OFTInteger),
        ("UpArea", ogr.OFTReal),
        ("idx", ogr.OFTInteger),
        ("idx_ds", ogr.OFTInteger),
        ("pit", ogr.OFTInteger),
    ):
        layer.CreateField(ogr.FieldDefn(name, field_type))
    for item in streams:
        coordinates = item.get("geometry", {}).get("coordinates", [])
        if len(coordinates) < 2:
            continue
        geometry = ogr.Geometry(ogr.wkbLineString)
        for x, y in coordinates:
            geometry.AddPoint_2D(float(x), float(y))
        properties = item.get("properties", {})
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(geometry)
        feature.SetField("Order", int(properties.get("strord", 1)))
        feature.SetField("UpArea", float(properties.get("uparea", 0.0)))
        feature.SetField("idx", int(properties.get("idx", -1)))
        feature.SetField("idx_ds", int(properties.get("idx_ds", -1)))
        feature.SetField("pit", int(bool(properties.get("pit", False))))
        layer.CreateFeature(feature)
        feature = None
    dataset = None
    if not os.path.exists(path):
        raise RuntimeError("Could not write the channel network.")
    return path


def _delineate(context, x, y, raster_path, basin_id):
    np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    inverse = ~context["transform"]
    col_value, row_value = inverse * (float(x), float(y))
    row, col = int(math.floor(row_value)), int(math.floor(col_value))
    reference = context["reference"]
    if row < 0 or col < 0 or row >= reference["rows"] or col >= reference["cols"]:
        raise ValueError("Outlet coordinates are outside the filled DEM grid.")
    if context["invalid"][row, col]:
        raise ValueError("Outlet coordinates are outside the valid filled DEM domain.")
    center_x, center_y = context["transform"] * (col + 0.5, row + 0.5)
    basin = context["flwdir"].basins(
        xy=([center_x], [center_y]), ids=[int(basin_id)]
    )
    mask = basin == int(basin_id)
    if not np.any(mask):
        raise RuntimeError("The delineated watershed is empty.")
    values = np.where(mask, int(basin_id), 0).astype(np.int32)
    raster_path = _write_raster(
        raster_path, values, reference, 0, gdal.GDT_Int32
    )
    area = context.get("area")
    if area is None:
        area = context["flwdir"].upstream_area(unit="m2")
        context["area"] = area
    catchment = float(area[row, col])
    if not np.isfinite(catchment) or catchment <= 0:
        catchment = int(np.count_nonzero(mask)) * pyflwdir_handler.cell_area_m2(reference)
    return {
        "raster_path": raster_path,
        "mask": mask,
        "cell_center": (float(center_x), float(center_y)),
        "catchment_area_m2": catchment,
    }


def delineate_outlet_file(filled_dem, x, y, raster_path, basin_id=1, *, task=None):
    """Delineate one outlet mask using no Qt or QGIS objects."""
    _cancelled(task)
    _progress(task, 10)
    result = _delineate(_flow_context(filled_dem), x, y, raster_path, basin_id)
    # The grid itself stays in the worker; only the written mask crosses back.
    result.pop("mask", None)
    _progress(task, 100)
    return result


def _dem_domain(context, basin_id, raster_path):
    np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    mask = ~context["invalid"]
    values = np.where(mask, int(basin_id), 0).astype(np.int32)
    raster = _write_raster(raster_path, values, context["reference"], 0, gdal.GDT_Int32)
    return raster, mask


def _merge_masks(masks, reference, output_path):
    """Write the union of every domain mask as one delineation raster.

    Domains nest, so a cell keeps the first domain that claims it: the outlets
    are delineated before the DEM extent, which would otherwise swallow them.
    Only the union matters downstream -- the merged mask is what the L0 rasters
    are cut to -- but keeping the ids makes the raster readable on its own.
    """
    np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    merged = None
    for domain_id, mask in masks:
        if merged is None:
            merged = np.where(mask, int(domain_id), 0).astype(np.int32)
            continue
        merged[(merged == 0) & mask] = int(domain_id)
    if merged is None:
        raise ValueError("Select at least one domain outlet.")
    return _write_raster(output_path, merged, reference, 0, gdal.GDT_Int32)


def domain_mask_bounds(mask_path):
    """Return the map bounds of the delineated cells in a mask raster.

    The mask spans the whole filled-DEM grid, so its raster extent is not the
    domain extent; the model grid is anchored on the non-zero cells instead.
    """
    np, _pfd, _affine, gdal, _ogr, _osr = _dependencies()
    dataset = gdal.Open(str(mask_path), gdal.GA_ReadOnly)
    if dataset is None:
        return None
    values = dataset.GetRasterBand(1).ReadAsArray()
    transform = dataset.GetGeoTransform()
    projection = dataset.GetProjection()
    dataset = None
    if values is None:
        return None
    rows, cols = np.nonzero(values)
    if rows.size == 0:
        return None
    return {
        "bounds": (
            transform[0] + int(cols.min()) * transform[1],
            transform[3] + (int(rows.max()) + 1) * transform[5],
            transform[0] + (int(cols.max()) + 1) * transform[1],
            transform[3] + int(rows.min()) * transform[5],
        ),
        "projection": projection,
    }


def merge_domain_masks(masks, output_path):
    """Merge saved masks one at a time, without retaining all outlet grids."""
    masks = iter(masks)
    first = next(masks, None)
    if first is None:
        raise ValueError("Select at least one domain outlet.")
    reference = _read_raster(first[1])
    first_mask = reference.pop("array") != 0

    def entries():
        yield int(first[0]), first_mask
        for domain_id, path in masks:
            raster = _read_raster(path)
            if any(raster[key] != reference[key] for key in ("rows", "cols", "geotransform", "projection")):
                raise ValueError(f"Domain mask does not match the common DEM grid: {path}")
            yield int(domain_id), raster["array"] != 0

    return _merge_masks(entries(), reference, output_path)


def delineate_domains_file(
    filled_dem,
    delineations,
    *,
    dem_domain=None,
    merged_path="",
    task=None,
):
    """Delineate all selected domains and merge their masks."""
    context = _flow_context(filled_dem)
    results = {}
    masks = []
    total = max(1, len(delineations) + int(dem_domain is not None))
    for index, item in enumerate(delineations):
        _cancelled(task)
        outlet_id, x, y, raster, domain_id, *domain_dem = item
        result = _delineate(context, x, y, raster, domain_id)
        masks.append((domain_id, result.pop("mask")))
        # Domain DEMs are written during Morphology Setup on the common L0 grid.
        if domain_dem:
            result["dem_path"] = str(domain_dem[0])
        results[str(outlet_id)] = result
        _progress(task, 80 * (index + 1) / total)
    if dem_domain is not None:
        domain_id, raster, *domain_dem = dem_domain
        _raster, mask = _dem_domain(context, domain_id, raster)
        dem_result = str(domain_dem[0]) if domain_dem else None
        masks.append((domain_id, mask))
    merged = _merge_masks(masks, context["reference"], merged_path)
    _progress(task, 100)
    return {
        "outlets": results,
        "dem_domain_path": dem_result if dem_domain is not None else None,
        "merged_path": merged,
    }


__all__ = [
    "crop_aligned_l0_raster",
    "delineate_domains_file",
    "delineate_outlet_file",
    "domain_mask_bounds",
    "gauge_position_file",
    "hydrology_files",
    "mask_aligned_l0_raster",
    "merge_domain_masks",
    "write_domain_dem_ascii",
]
