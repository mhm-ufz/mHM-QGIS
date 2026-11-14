"""The Resolution page: the L0, L1, L11/L3 and L2 grids from reference files or typed resolutions.

The L0 reference file fixes the L0 lattice; every other level is a whole multiple of L0. Set
intersects the ticked file extents, snaps that box inward to whole blocks of lcm(level factors)
L0 cells on the L2 lattice, checks the four headers against mHM's rules and saves them as the
common grid. The DEM and every meteorology input must cover that common extent, so nothing is
padded.
"""
from __future__ import annotations

import math
from pathlib import Path

from qgis.PyQt.QtCore import QSignalBlocker
from qgis.PyQt.QtGui import QCursor
from qgis.PyQt.QtWidgets import QMessageBox, QToolTip

from ...core.executions.morphology import setup as morphology_setup
from ...core.grid import (
    Extent,
    Grid,
    crs_equal,
    display_precision_for_unit,
    format_resolution,
    header_bounds,
    header_for_model_level,
    inner_bounds,
    integer_resolution_factor,
    is_geographic_unit,
    target_grid_from_header,
    validate_grid_headers,
    validate_mhm_grid,
)
from ...core.grid.raster import raster_grid
from ...core.handlers.file.netcdf.raster import source as raster_source
from ...core.handlers.state import processing
from ...core.handlers.store.paths import temp_morphology_folder
from ...qgis_bridge import layers as qgis_layers
from .version import selected as selected_version
from .workflow_state import invalidate as invalidate_workflows
from .workflow_state import invalidate_meteo

LEVELS = ("L0", "L1", "L11", "L2")
CORNERS = (("nCols", "ncols"), ("nRows", "nrows"), ("xLowerLeft", "xllcorner"), ("yLowerLeft", "yllcorner"))
CHANGED = "#1565c0"


def _domain(dialog):
    crs = dialog.get_crs()
    return qgis_layers.crs_string(crs), qgis_layers.crs_unit(crs)


def _require_crs(dialog):
    if not dialog.setup_folder:
        QMessageBox.warning(dialog, "Domain setup", "Create a new domain setup or open an existing "
                            "one (Open) first.")
        return False
    if not dialog._set_crs or not dialog.get_crs().isValid():
        QMessageBox.warning(dialog, "Domain setup", "Set the domain CRS first.")
        return False
    return True


# --- Reference files --------------------------------------------------------

def _read(dialog, selection):
    """The file's grid in the domain CRS, and the extent its data cover there: the grid itself,
    or, from another CRS, the inner box of the transformed footprint."""
    domain, unit = _domain(dialog)
    path, crs = raster_source(selection["path"], selection.get("variable")), selection["crs"]
    info = raster_grid(path, crs, domain, unit)
    same = crs_equal(crs, domain)
    bounds = header_bounds(info["header"]) if same else inner_bounds(
        header_bounds(raster_grid(path, crs, crs)["header"]), crs, domain)
    return {"info": info, "extent": Extent.from_bounds(bounds), "same_crs": same}


def l0_factor(dialog):
    return int((dialog._resolution_files.get("L0") or {}).get("factor") or 1)


def _l0_info(dialog):
    """The L0 grid: the L0 file in the domain CRS, upscaled by its factor."""
    selection, (domain, unit) = dialog._resolution_files["L0"], _domain(dialog)
    return raster_grid(raster_source(selection["path"], selection.get("variable")), selection["crs"],
                       domain, unit, l0_factor(dialog))


def load_file(dialog, level):
    """Read `level`'s saved reference file into the From File row; False when it is unreadable."""
    selection = dialog._resolution_files[level]
    try:
        dialog._level_file_info[level] = _read(dialog, selection)
        if level == "L0":
            dialog._grid_l0_info = _l0_info(dialog)
    except (OSError, RuntimeError, ValueError) as error:
        dialog._level_file_info.pop(level, None)
        if level == "L0":
            dialog._grid_l0_info = None
        dialog.log_message(f"WARNING: {level} reference file {selection['path']}: {error}")
        show_file_row(dialog, level)
        return False
    show_file_row(dialog, level)
    return True


def reload_files(dialog):
    """Re-read every saved reference file (headers only), e.g. in a new domain CRS."""
    dialog._level_file_info, dialog._grid_l0_info = {}, None
    for level in LEVELS:
        if level in dialog._resolution_files:
            load_file(dialog, level)
        else:
            show_file_row(dialog, level)
    show_l0(dialog)


def _file_name(dialog, level):
    selection = dialog._resolution_files.get(level)
    if not selection:
        return ""
    name = Path(selection["path"]).name
    return name if level in dialog._level_file_info else f"missing: {name}"


def show_file_row(dialog, level):
    """The From File row: the file's name and its grid in the domain CRS, as read."""
    selection = dialog._resolution_files.get(level)
    file = dialog._level_file_info.get(level)
    description = getattr(dialog, f"label_description{level}File")
    description.setText(_file_name(dialog, level))
    description.setToolTip(selection["path"] if selection else "")
    header = file["info"]["header"] if file else {}
    unit = file["info"]["unit"] if file else ""
    for name, key in CORNERS:
        value = header.get(key)
        text = "" if value is None else str(value) if key in ("ncols", "nrows") else format_resolution(
            value, unit, precision=display_precision_for_unit(unit))
        getattr(dialog, f"label_{name}{level}File").setText(text)
    cell = file["info"]["resolution"] if file else None
    getattr(dialog, f"label_file{level}ResolutionValue").setText(format_resolution(cell, unit) if cell else "")
    getattr(dialog, f"label_file{level}ResolutionUnit").setText(unit if cell else "")
    if level != "L0":
        l0 = current_l0_resolution(dialog)
        getattr(dialog, f"label_file{level}ResolutionMultiplier").setText(f"{cell / l0:.6g}" if cell and l0 else "")
    getattr(dialog, f"checkBox_extent{level}enforce").setEnabled(bool(file))


def browse_file(dialog, level):
    """A Browse… of the Resolution page: the level's reference file, its grid and resolution."""
    if not _require_crs(dialog):
        return
    l0 = current_l0_resolution(dialog)
    if level != "L0" and not l0:
        QMessageBox.warning(dialog, "Resolution", "Browse the L0 reference file first.")
        return
    from ..dialogs.browse_resolution_L0_file import L0FileDialog
    from ..dialogs.browse_resolution_L2_L1_L11_file import LevelFileDialog

    kind = L0FileDialog if level == "L0" else LevelFileDialog
    prompt = kind(level, dialog.get_crs(), dialog._resolution_files.get(level), dialog)
    if not prompt.exec():
        return
    dialog._resolution_files[level] = prompt.selection()
    if load_file(dialog, level):
        if level == "L0":
            show_l0(dialog)
            rescale(dialog)
        else:
            cell = dialog._level_file_info[level]["info"]["resolution"]
            ratio = cell / l0
            n = max(1, round(ratio))
            if abs(ratio - n) > 1e-7 * ratio:
                dialog.log_message(f"WARNING: The {level} file's cell ({cell:.10g}) is {ratio:.6g} x L0; "
                                   f"{level} uses {n} x L0, so its data are resampled.")
            set_multiplier(dialog, level, n)
    changed(dialog)


# --- Resolutions -------------------------------------------------------------

def show_l0(dialog):
    """The L0 line edit: the L0 file's cell x its factor; the units of the domain CRS."""
    l0 = current_l0_resolution(dialog)
    with QSignalBlocker(dialog.lineEdit_L0Resolution):
        dialog.lineEdit_L0Resolution.setText(repr(l0) if l0 else "")
    unit = _domain(dialog)[1]
    for level in LEVELS:
        combo = getattr(dialog, f"comboBox_{level}ResolutionUnit")
        combo.clear()
        combo.addItem(unit)
        combo.setEnabled(False)  # grid units belong to the domain CRS
        if level != "L0":
            sync_multiplier(dialog, level)
            show_file_row(dialog, level)  # its x L0 ratio


def rescale(dialog):
    """A new L0: every level keeps its multiplier, so its resolution follows."""
    for level in ("L1", "L11", "L2"):
        if entered(dialog, level):
            multiplier_changed(dialog, level, getattr(dialog, f"spinBox_{level}ResolutionMultiplier").value())


def snap_l0(dialog):
    """A typed L0 becomes the nearest whole multiple of the L0 file's cell, its new factor."""
    file, value = dialog._level_file_info.get("L0"), entered(dialog, "L0")
    if file and value:
        header = file["info"]["header"]
        factor = min(max(1, round(value / file["info"]["resolution"])), header["ncols"], header["nrows"])
        if factor != l0_factor(dialog):
            dialog._resolution_files["L0"]["factor"] = factor
            dialog._grid_l0_info = _l0_info(dialog)
            show_l0(dialog)
            rescale(dialog)
            changed(dialog)
    show_l0(dialog)


def set_multiplier(dialog, level, n):
    spin = getattr(dialog, f"spinBox_{level}ResolutionMultiplier")
    with QSignalBlocker(spin):
        spin.setValue(n)
    multiplier_changed(dialog, level, n)


def sync_multiplier(dialog, level, snap=False):
    """Show the line edit's cell size as n x L0; `snap` rewrites the text to exactly n x L0."""
    l0, value = current_l0_resolution(dialog), entered(dialog, level)
    spin = getattr(dialog, f"spinBox_{level}ResolutionMultiplier")
    spin.setEnabled(bool(l0))
    if l0 and value:
        with QSignalBlocker(spin):
            spin.setValue(max(1, round(value / l0)))
        if snap:
            getattr(dialog, f"lineEdit_{level}Resolution").setText(repr(spin.value() * l0))


def multiplier_changed(dialog, level, n):
    if l0 := current_l0_resolution(dialog):
        getattr(dialog, f"lineEdit_{level}Resolution").setText(repr(n * l0))


def changed(dialog):
    """A grid input changed: the Set headers no longer apply."""
    if dialog._loading_input_state:
        return
    clear_headers(dialog)
    dialog.save_input_state()


def current_l0_resolution(dialog):
    info = dialog._grid_l0_info
    return float(info["resolution"]) if info else None


def current_l1_resolution(dialog):
    return entered(dialog, "L1")


def current_l11_resolution(dialog):
    return entered(dialog, "L11")


def current_grid_unit(dialog):
    return (dialog._grid_l0_info or {}).get("unit", "")


def entered(dialog, level):
    """A positive typed cell size, or None."""
    try:
        entered = float(getattr(dialog, f"lineEdit_{level}Resolution").text())
    except (TypeError, ValueError):
        return None
    return entered if math.isfinite(entered) and entered > 0 else None


def ticked(dialog):
    return [level for level in LEVELS if getattr(dialog, f"checkBox_extent{level}enforce").isChecked()]


# --- The common grid ---------------------------------------------------------

def grid_fingerprint(dialog):
    from ...core.handlers.state.cache import fingerprint

    files = dialog._resolution_files
    return fingerprint([files[level]["path"] for level in LEVELS if level in files], {
        "crs": dialog.selected_meteo_crs(), "files": files, "extent": ticked(dialog),
        "resolutions": [entered(dialog, level) for level in ("L1", "L11", "L2")], "grid_version": 4})


def grid_current(dialog):
    """Whether the saved common grid still matches the page's inputs."""
    grid = dialog._grid_l2_metadata or {}
    try:
        return bool(grid.get("l1_header")) and grid.get("configuration_fingerprint") == grid_fingerprint(dialog)
    except (OSError, ValueError):
        return False


def require_common_grid(dialog):
    if not grid_current(dialog):
        raise ValueError("Set the resolutions (Resolution page) to establish the common grid first.")
    return dialog._grid_l2_metadata


def common_l0_header(dialog):
    """The L0 header of the last Set common grid, or None; Execute All refuses a stale one."""
    return (dialog._grid_l2_metadata or {}).get("l0_header")


def grid_level_headers(dialog):
    """The L0, L1, L11 and L2 headers of the Set common grid."""
    grid = require_common_grid(dialog)
    return {level: dict(grid[f"{level.lower()}_header"]) for level in LEVELS}


def build_grid(dialog):
    """The common grid's processing metadata, with all four headers; ValueError says what to fix."""
    l0_info = dialog._grid_l0_info
    if not l0_info:
        raise ValueError("Browse the L0 reference file (Browse… in the L0 row).")
    crs, unit = _domain(dialog)
    if not (is_geographic_unit(unit) or unit == "m"):
        raise ValueError(f"The domain CRS must be in metres or degrees: mHM computes cell areas from them "
                         f"(this CRS is in {unit or 'an unknown unit'}).")
    l0 = float(l0_info["resolution"])
    factors = {}
    for level in ("L1", "L11", "L2"):
        value = entered(dialog, level)
        if not value:
            raise ValueError(f"Enter the {level} resolution, or browse its file.")
        factors[level] = integer_resolution_factor(value, l0)
        if factors[level] is None:
            raise ValueError(f"{level} ({value:.10g}) must be a whole multiple of L0 ({l0:.10g}), not finer.")
    levels = ticked(dialog)
    if not levels:
        raise ValueError("Tick at least one extent in the Extent row: the common extent lies inside "
                         "every ticked file.")
    box, names, margin = None, [], 0.01 * l0  # 1% of an L0 cell absorbs lat/lon rounding, as coverage does
    for level in levels:
        if level not in dialog._level_file_info:
            raise ValueError(f"The {level} extent is ticked, but {level} has no readable file.")
        extent = dialog._level_file_info[level]["extent"]
        extent = Extent(extent.xmin - margin, extent.xmax + margin, extent.ymin - margin, extent.ymax + margin)
        joined = extent if box is None else box.intersect(extent)
        if joined is None:
            raise ValueError(f"The {level} file does not overlap the {' and '.join(names)} extent: "
                             "the ticked extents must share an area.")
        box, names = joined, [*names, level]
    block = math.lcm(factors["L1"], factors["L11"], factors["L2"])
    anchor, raw = "the L0 file's lattice", {}
    l2_file = dialog._level_file_info.get("L2")
    if l2_file and l2_file["same_crs"]:
        info = l2_file["info"]
        raw = {"raw_l2_resolution": (info["x_resolution"], info["y_resolution"]),
               "raw_l2_extent": Extent.from_header(info["header"]), "raw_l2_crs": crs}
    grid = Grid.for_meteorology(l0_info["header"], box.bounds, factors["L2"], block=block, crs=crs, unit=unit,
                                **raw)
    if not grid.requires_l2_resampling:
        anchor = "the L2 file's lattice"
    headers = {"L0": grid.l0_header, "L2": grid.l2_header, **{
        level: header_for_model_level(grid.l2_header, factors[level] * l0, l0, level) for level in ("L1", "L11")}}
    headers = validate_grid_headers(headers)[0]
    validate_mhm_grid(headers, selected_version(dialog))
    metadata = grid.metadata(extent_source=", ".join(names), source_file=(dialog._resolution_files.get("L2") or {}).get(
        "path", ""))
    target = target_grid_from_header(headers["L2"], crs or None)
    metadata.update(
        {f"{level.lower()}_header": headers[level] for level in LEVELS},
        study_bounds=list(Extent.from_header(headers["L0"]).bounds),
        extent_note=(f"Common extent: the intersection of the ticked extents ({', '.join(names)}), snapped inward "
                     f"to whole blocks of lcm({factors['L1']}, {factors['L11']}, {factors['L2']}) = {block} L0 "
                     f"cells ({format_resolution(block * l0, unit)} {unit}) on {anchor}."),
        configuration_fingerprint=grid_fingerprint(dialog),
    )
    if target.sample_lon is not None:
        metadata["wgs84_bounds"] = (float(target.sample_lon.min()), float(target.sample_lon.max()),
                                    float(target.sample_lat.min()), float(target.sample_lat.max()))
    return metadata


def set_resolution(dialog):
    """The Resolution page's Set: the common grid, its Updated Headers and latlon.nc."""
    if not _require_crs(dialog):
        return False
    dialog.log_selected_input_paths("Set resolutions")
    try:
        metadata = build_grid(dialog)
    except ValueError as error:
        clear_headers(dialog)
        QMessageBox.warning(dialog, "Resolution", str(error))
        dialog.save_input_state()
        return False
    previous = processing.grid_metadata(dialog.setup_folder) or {}
    if previous.get("configuration_fingerprint") != metadata["configuration_fingerprint"]:
        invalidate_workflows(dialog, "execute_all_morphology", "execute_all_meteo", "execute_all_observations",
                             "run_mhm")
    dialog._grid_l2_metadata = processing.save_grid_metadata(dialog.setup_folder, metadata)
    show_headers(dialog)
    check_dem(dialog)
    dialog.log_message(f"Common grid set: L2 {format_resolution(metadata['l2_resolution'], metadata['l2_unit'])} "
                       f"{metadata['l2_unit']} ({metadata['l2_ratio_to_l0']} x L0). {metadata['extent_note']}")
    outputs = morphology_setup.latlon(morphology_setup.SetupRequest(
        setup_folder=dialog.setup_folder, headers=grid_level_headers(dialog), crs=dialog.selected_meteo_crs(),
    ), log=dialog.log_message)
    if outputs:
        dialog.log_message(f"latlon.nc created successfully: {outputs[0]}")
    dialog.save_input_state()  # the latlon finding, so Resolution's status
    return bool(outputs)


def _steps(dialog, level, header, file):
    """What happens to `level`'s file on the common grid, as the Description column lists it."""
    info, steps = file["info"], []
    if not file["same_crs"]:
        steps.append(f"warped {dialog._resolution_files[level]['crs']} → {dialog.selected_meteo_crs()}")
    if level == "L0" and l0_factor(dialog) > 1:
        steps.append(f"upscaled x {l0_factor(dialog)}")
    cell = float(header["cellsize"])
    if level != "L0" and not math.isclose(info["resolution"], cell, rel_tol=1e-7):
        steps.append(f"cell {info['resolution']:.6g} → {cell:.6g}, resampled")
    gap = (dialog._grid_l2_metadata or {}).get("alignment_gap")
    if level == "L2" and gap and any(abs(value) > 1e-9 * cell for value in gap):  # however small: it is real
        steps.append(f"shifted by ({gap[0]:.6g}, {gap[1]:.6g}) onto L0")
    source, common = Extent.from_header(info["header"]), Extent.from_header(header)
    sides = {"W": common.xmin - source.xmin, "E": source.xmax - common.xmax,
             "S": common.ymin - source.ymin, "N": source.ymax - common.ymax}
    noise = 0.01 * float(dialog._grid_l2_metadata["l0_resolution"])  # lat/lon rounding, as coverage allows
    cropped = [f"{side} {value / cell:.4g}" for side, value in sides.items() if abs(value) > noise]
    if cropped:
        steps.append("edge cells cropped (-) or added (+, beyond the file): " + ", ".join(cropped))
    return "; ".join(steps) or "used as is"


def show_headers(dialog):
    """The Set grid in Updated Headers: a value that differs from the From File row is blue, with an
    ⓘ giving the reason; the Description column lists what happens to each file."""
    grid = dialog._grid_l2_metadata
    unit, noise = grid.get("l0_unit", ""), 0.01 * float(grid["l0_resolution"])  # lat/lon rounding
    dialog._header_notes = {}
    for level in LEVELS:
        header, file = grid[f"{level.lower()}_header"], dialog._level_file_info.get(level)
        source = file["info"]["header"] if file else None
        for name, key in (*CORNERS, ("cellSize", "cellsize")):
            value = header[key]
            text = str(int(value)) if key in ("ncols", "nrows") else format_resolution(
                value, unit, precision=None if key == "cellsize" else display_precision_for_unit(unit))
            label = getattr(dialog, f"label_{name}{level}Common")
            reason = source and not math.isclose(float(source[key]), float(value), rel_tol=1e-9,
                                                 abs_tol=noise if key in ("xllcorner", "yllcorner") else 0) and (
                _cell_reason(dialog, level, file, header) if key == "cellsize" else _extent_reason(level, file, header,
                                                                                                    grid, unit))
            if reason:
                dialog._header_notes[f"{level}:{key}"] = reason
                label.setText(f'<span style="color:{CHANGED}">{text}</span> <a href="{level}:{key}">ⓘ</a>')
            else:
                label.setText(text)
        getattr(dialog, f"label_display{level}GridCommon").setText(f'<a href="{level}">👁</a>')
        if file:
            getattr(dialog, f"label_description{level}File").setText(_steps(dialog, level, header, file))
    for label, value in zip((dialog.label_minimumEasting, dialog.label_maximumEasting, dialog.label_minimumNorthing,
                             dialog.label_maximumNorthing), header_bounds(grid["l0_header"]), strict=True):
        label.setText(format_resolution(value, unit, precision=display_precision_for_unit(unit)))


def _cell_reason(dialog, level, file, header):
    cell, l0 = float(header["cellsize"]), float(dialog._grid_l2_metadata["l0_resolution"])
    if level == "L0":
        return f"The L0 file's cell ({file['info']['resolution']:.10g}) x the upscale factor {l0_factor(dialog)}."
    return (f"The file's cell ({file['info']['resolution']:.10g}) is {file['info']['resolution'] / l0:.6g} x L0; "
            f"{level} is {round(cell / l0)} x L0 = {cell:.10g}, so its data are resampled.")


def _extent_reason(level, file, header, grid, unit):
    source, common = Extent.from_header(file["info"]["header"]), Extent.from_header(header)
    moves = (f"west {common.xmin - source.xmin:+.6g}, east {common.xmax - source.xmax:+.6g}, "
             f"south {common.ymin - source.ymin:+.6g}, north {common.ymax - source.ymax:+.6g} {unit}")
    return f"{grid.get('extent_note', '')} Its edges against the {level} file: {moves}."


def explain(dialog, href):
    """An ⓘ of Updated Headers: why that value differs from the file's."""
    QToolTip.showText(QCursor.pos(), dialog._header_notes.get(href, ""), dialog)


def clear_headers(dialog):
    """Empty Updated Headers and the common extent; the Description column names the files again."""
    dialog._header_notes = {}
    for level in LEVELS:
        for name, _key in (*CORNERS, ("cellSize", "cellsize")):
            getattr(dialog, f"label_{name}{level}Common").setText("")
        getattr(dialog, f"label_display{level}GridCommon").setText("")
        getattr(dialog, f"label_description{level}File").setText(_file_name(dialog, level))
    for label in (dialog.label_minimumEasting, dialog.label_maximumEasting, dialog.label_minimumNorthing,
                  dialog.label_maximumNorthing):
        label.setText("")


def restore(dialog, state):
    """The saved files, ticks and resolutions, and the Set headers while they still apply. Reads the
    files' headers only, never meteorology."""
    files = state.get("resolution_files") or {}
    dialog._resolution_files = {level: dict(value) for level, value in files.items()
                                if level in LEVELS and isinstance(value, dict) and value.get("path")}
    extent = state.get("extent_levels", ["L0"])
    for level in LEVELS:
        box = getattr(dialog, f"checkBox_extent{level}enforce")
        with QSignalBlocker(box):
            box.setChecked(level in extent)
    saved = state.get("grid_resolutions") or {}
    for level in ("L1", "L11", "L2"):
        edit = getattr(dialog, f"lineEdit_{level}Resolution")
        try:
            value = float(saved.get(level.lower()) or 0)
        except (TypeError, ValueError):
            value = 0
        with QSignalBlocker(edit):
            edit.setText(repr(value) if value > 0 else "")
    reload_files(dialog)
    dialog._grid_l2_metadata = processing.grid_metadata(dialog.setup_folder) if dialog.setup_folder else None
    if grid_current(dialog):
        show_headers(dialog)
    else:
        clear_headers(dialog)
    check_dem(dialog)


def clear(dialog):
    """No setup open: no files, resolutions or grid; L0's extent ticked."""
    dialog._resolution_files, dialog._level_file_info = {}, {}
    dialog._grid_l0_info = dialog._grid_l2_metadata = None
    for level in LEVELS:
        box = getattr(dialog, f"checkBox_extent{level}enforce")
        with QSignalBlocker(box):
            box.setChecked(level == "L0")
        edit = getattr(dialog, f"lineEdit_{level}Resolution")
        with QSignalBlocker(edit):
            edit.clear()
        if level != "L0":
            set_multiplier(dialog, level, 1)
            edit.clear()
        show_file_row(dialog, level)
    clear_headers(dialog)
    show_l0(dialog)
    dialog._dem_error = None
    dialog.label_demDescription.setText("")


def set_crs(dialog):
    """Apply a changed domain CRS: the files are read in it again, and every run is due."""
    if not dialog.setup_folder:
        QMessageBox.warning(dialog, "Domain setup", "Create a new domain setup or open an existing "
                            "one (Open) before setting the CRS.")
        return
    authid = dialog.get_crs().authid()
    if authid == dialog._set_crs:
        return
    dialog._set_crs = authid
    reload_files(dialog)
    rescale(dialog)
    invalidate_workflows(dialog, "execute_all_morphology")
    invalidate_meteo(dialog)
    changed(dialog)
    check_dem(dialog)


# --- DEM ---------------------------------------------------------------------

def _dem_note(dialog):
    """What happens to the DEM on the common L0 grid; ValueError when it cannot fill it."""
    layer = dialog.input_combo("dem").currentLayer()
    if layer is None:
        raise ValueError("Select the DEM.")
    if not grid_current(dialog):
        raise ValueError("Set the resolutions first (Resolution page): the DEM must lie on the common L0 grid.")
    l0 = dialog._grid_l2_metadata["l0_header"]
    domain, unit = _domain(dialog)
    data = dialog.input_combo("dem").currentData()
    crs = (data.get("source_crs", "") if isinstance(data, dict) else "") or qgis_layers.crs_string(layer.crs())
    source, factor = dialog.input_combo("dem").source_uri() or layer.source(), dialog._upscale_factor
    dem = raster_grid(source, crs, domain, unit, factor)["header"]
    extent = Extent.from_bounds(header_bounds(raster_grid(source, crs, domain, unit)["header"]) if crs_equal(
        crs, domain) else inner_bounds(header_bounds(raster_grid(source, crs, crs)["header"]), crs, domain))
    cell = float(l0["cellsize"])
    if not math.isclose(float(dem["cellsize"]), cell, rel_tol=1e-9):
        raise ValueError(f"The DEM x {factor} has cells of {float(dem['cellsize']):.10g} {unit}, but L0 is "
                         f"{cell:.10g} {unit}: set the upscale factor to L0 / DEM cell, or choose another L0.")
    offsets = [(float(dem[key]) - float(l0[key])) / cell for key in ("xllcorner", "yllcorner")]
    if any(abs(value - round(value)) > 1e-6 for value in offsets):
        raise ValueError(f"The DEM grid is off the L0 lattice by ({offsets[0] % 1:.4g}, {offsets[1] % 1:.4g}) "
                         "cells: it must lie a whole number of L0 cells from the common grid.")
    common, tolerance = Extent.from_header(l0), 0.01 * cell  # lat/lon rounding, as coverage allows
    sides = {"W": common.xmin - extent.xmin, "E": extent.xmax - common.xmax,
             "S": common.ymin - extent.ymin, "N": extent.ymax - common.ymax}
    if min(sides.values()) < -tolerance:
        raise ValueError(f"The DEM ({', '.join(f'{v:.10g}' for v in extent.bounds)}) does not cover the common "
                         f"extent ({', '.join(f'{v:.10g}' for v in common.bounds)}): use a DEM that covers it, "
                         "or change the ticked extents.")
    steps = [] if crs_equal(crs, domain) else [f"warped {crs} → {domain}"]
    if factor > 1:
        steps.append(f"topography upscaled x {factor} (mhm-tools)")
    cropped = [f"{side} {value / cell:.4g}" for side, value in sides.items() if value > tolerance]
    steps.append("covers the common extent" + (f"; L0 cells cropped: {', '.join(cropped)}" if cropped else ""))
    return "; ".join(steps)


def check_dem(dialog):
    """Cache why the DEM cannot fill the common L0 grid (None when it can) and show it as the DEM note."""
    try:
        note, dialog._dem_error = _dem_note(dialog), None
    except (OSError, RuntimeError, ValueError) as error:
        note = dialog._dem_error = str(error)
    dialog.label_demDescription.setText(note)
    return dialog._dem_error


def set_dem(dialog):
    """The DEM page's Set: its upscale factor, checked against the common L0 grid."""
    factor = dialog.spinBox_upscaleTopographyFactor.value() if dialog.groupBox_upscaleTopography.isChecked() else 1
    previous, dialog._upscale_factor = dialog._upscale_factor, factor
    if factor != previous and dialog.setup_folder:
        invalidate_workflows(dialog, "execute_all_morphology")
        for path in Path(temp_morphology_folder(dialog.setup_folder)).glob("2_channel_network.*"):
            path.unlink()  # drawn on the old L0: Set Gauges again
    if error := check_dem(dialog):
        QMessageBox.warning(dialog, "DEM", error)
    dialog.save_input_state()
