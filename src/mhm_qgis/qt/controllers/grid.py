"""The Resolution page: the L0, L1, L11/L3 and L2 grids from reference files or typed resolutions.

The L0 reference file fixes the L0 lattice; every other level is a whole multiple of L0. Set
intersects the ticked file extents, snaps that box outward to whole blocks of lcm(level factors)
L0 cells on the L2 lattice, checks the four headers against mHM's rules and saves them as the
common grid. The meteorology must cover it (its cells are never padded); the DEM is shifted onto
the L0 lattice (under half a cell), then cropped or padded with no data to it.
"""
from __future__ import annotations

import math
from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem
from qgis.PyQt.QtCore import QSignalBlocker
from qgis.PyQt.QtWidgets import QMessageBox

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
from ..project_tree import ICONS
from .version import selected as selected_version
from .workflow_state import invalidate as invalidate_workflows
from .workflow_state import invalidate_meteo

LEVELS = ("L0", "L1", "L11", "L2")
CORNERS = (("nCols", "ncols"), ("nRows", "nrows"), ("xLowerLeft", "xllcorner"), ("yLowerLeft", "yllcorner"))
CHANGED = "#1565c0"
DERIVATIVES = {"slope": "slope", "aspect": "aspect", "flow_direction": "fdir", "flow_accumulation": "facc"}


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
    dialog._displayed.pop(level, None)  # a new file: its own layer when shown
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


def show_file_row(dialog, level):
    """The From File row: the file's grid in the domain CRS, as read; its Browse… names the file."""
    selection = dialog._resolution_files.get(level)
    file = dialog._level_file_info.get(level)
    getattr(dialog, f"pushButton_browse{level}File").setToolTip(
        "" if not selection else selection["path"] if file else f"Missing: {selection['path']}")
    _eye(dialog, level, shown=bool(file))
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
        extent = dialog._level_file_info[level]["extent"]  # shrunk: a rounded edge does not snap a block out
        extent = Extent(extent.xmin + margin, extent.xmax - margin, extent.ymin + margin, extent.ymax - margin)
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
    if l2_file:
        _inside_meteo(grid, l2_file, f"snapped outward to blocks of lcm({factors['L1']}, {factors['L11']}, "
                      f"{factors['L2']}) = {block} L0 cells", margin)
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
        extent_note=(f"Common extent: the intersection of the ticked extents ({', '.join(names)}), snapped outward "
                     f"to whole blocks of lcm({factors['L1']}, {factors['L11']}, {factors['L2']}) = {block} L0 "
                     f"cells ({format_resolution(block * l0, unit)} {unit}) on {anchor}."),
        configuration_fingerprint=grid_fingerprint(dialog),
    )
    if target.sample_lon is not None:
        metadata["wgs84_bounds"] = (float(target.sample_lon.min()), float(target.sample_lon.max()),
                                    float(target.sample_lat.min()), float(target.sample_lat.max()))
    return metadata


def _inside_meteo(grid, l2_file, how, margin):
    """The meteo cells are never padded: the common extent must lie inside the L2 file's cells (where
    its lattice anchors the grid, as shifted onto L0)."""
    extent, cell = l2_file["extent"], float(l2_file["info"]["resolution"])
    if not grid.requires_l2_resampling and grid.alignment_gap:
        dx, dy = grid.alignment_gap
        extent = Extent(extent.xmin + dx, extent.xmax + dx, extent.ymin + dy, extent.ymax + dy)
    common = grid.common_extent
    beyond = {"west": extent.xmin - common.xmin, "east": common.xmax - extent.xmax,
              "south": extent.ymin - common.ymin, "north": common.ymax - extent.ymax}
    short = [f"{value / cell:.4g} cell{'' if round(value / cell, 4) == 1 else 's'} {side}"
             for side, value in beyond.items() if value > margin]
    if short:
        raise ValueError(f"The common extent ({how}) reaches beyond the L2 (meteo) grid: {', '.join(short)}. "
                         "The meteo cells are never padded: tick the L2 extent, use meteo covering more, or "
                         "L1/L11 factors that divide the L2 factor.")


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


def _edges(source, common, cell, noise, sides=("west", "east", "south", "north")):
    """What the common extent crops from or adds to `source` on each side, in its `cell`s."""
    moves = {"west": (common.xmin - source.xmin, "column"), "east": (source.xmax - common.xmax, "column"),
             "south": (common.ymin - source.ymin, "row"), "north": (source.ymax - common.ymax, "row")}
    cropped, added = [], []
    for side in sides:
        value, unit = moves[side]
        if abs(value) > noise:
            count = round(abs(value) / cell, 2)
            (cropped if value > 0 else added).append(f"{count:g} {unit}{'' if count == 1 else 's'} {side}")
    return ([f"Cropped: {', '.join(cropped)}."] if cropped else []) + (
        [f"Padded with no data: {', '.join(added)}."] if added else [])


def _steps(dialog, level, header, file):
    """What happens to `level`'s file on the common grid, one step a line (its info button)."""
    info, steps = file["info"], []
    cell, l0 = float(header["cellsize"]), float(dialog._grid_l2_metadata["l0_resolution"])
    if not file["same_crs"]:
        steps.append(f"Warped {dialog._resolution_files[level]['crs']} → {dialog.selected_meteo_crs()}.")
    if level == "L0" and l0_factor(dialog) > 1:
        steps.append(f"Upscaled x {l0_factor(dialog)} ({info['resolution']:.6g} → {cell:.6g} cells).")
    if level != "L0" and not math.isclose(info["resolution"], cell, rel_tol=1e-7):
        steps.append(f"Cell {info['resolution']:.6g} = {info['resolution'] / l0:.6g} x L0: resampled to "
                     f"{round(cell / l0)} x L0 ({cell:.6g}).")
    gap = dialog._grid_l2_metadata.get("alignment_gap")
    if level == "L2" and gap and any(abs(value) > 1e-9 * cell for value in gap):  # however small: it is real
        steps.append(f"Shifted by ({gap[0]:.6g}, {gap[1]:.6g}) onto the L0 lattice.")
    steps += _edges(Extent.from_header(info["header"]), Extent.from_header(header), info["resolution"], 0.01 * l0)
    return "\n".join(steps) or "Used as is."


def show_headers(dialog):
    """The Set grid in Updated Headers: a value that differs from the From File row is blue, with an
    ⓘ giving the reason; each file row's info button lists what happens to its file."""
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
                _cell_reason(dialog, level, file, header) if key == "cellsize" else _extent_reason(
                    level, key, file, header, grid))
            if reason:
                dialog._header_notes[f"{level}:{key}"] = reason
                label.setText(f'<span style="color:{CHANGED}">{text}</span> <a href="{level}:{key}">ⓘ</a>')
            else:
                label.setText(text)
        _eye(dialog, f"grid:{level}")
        button = getattr(dialog, f"toolButton_description{level}File")
        button.setToolTip(_steps(dialog, level, header, file) if file else "")
        button.setEnabled(bool(file))
    for label, value in zip((dialog.label_minimumEasting, dialog.label_maximumEasting, dialog.label_minimumNorthing,
                             dialog.label_maximumNorthing), header_bounds(grid["l0_header"]), strict=True):
        label.setText(format_resolution(value, unit, precision=display_precision_for_unit(unit)))


def _cell_reason(dialog, level, file, header):
    cell, l0 = float(header["cellsize"]), float(dialog._grid_l2_metadata["l0_resolution"])
    if level == "L0":
        return (f"The L0 file's cell ({file['info']['resolution']:.10g}) x the upscale factor "
                f"{l0_factor(dialog)} = {cell:.10g}.")
    return (f"The file's cell ({file['info']['resolution']:.10g}) is {file['info']['resolution'] / l0:.6g} x L0; "
            f"{level} is {round(cell / l0)} x L0 = {cell:.10g}, so its data are resampled.")


def _extent_reason(level, key, file, header, grid):
    """Why a corner or count of the common grid differs from the file's, side by side."""
    source, common = Extent.from_header(file["info"]["header"]), Extent.from_header(header)
    cell, unit = file["info"]["resolution"], grid.get("l0_unit", "")
    precision = display_precision_for_unit(unit)
    if key in ("xllcorner", "yllcorner"):
        edge, sides = ("West", ("west",)) if key == "xllcorner" else ("South", ("south",))
        what = (f"{edge} edge at {format_resolution(header[key], unit, precision=precision)} instead of the "
                f"{level} file's {format_resolution(file['info']['header'][key], unit, precision=precision)}.")
    else:
        kind, sides = ("columns", ("west", "east")) if key == "ncols" else ("rows", ("south", "north"))
        what = (f"{header[key]} {kind} of {float(header['cellsize']):.6g} instead of the {level} file's "
                f"{file['info']['header'][key]} of {cell:.6g}.")
    edges = _edges(source, common, cell, 0.01 * float(grid["l0_resolution"]), sides)
    return "\n".join([grid.get("extent_note", ""), what, *edges])


def _eye(dialog, key, shown=True):
    """A Display label (`L2`: the level's file, `grid:L2`: its Set grid): the project tree's eye when
    on the map, crossed out when hidden; empty when there is nothing to show."""
    level = key.removeprefix("grid:")
    label = getattr(dialog, f"label_display{level}GridCommon" if key.startswith("grid:") else f"label_display{level}File")
    on = key in dialog._displayed
    icon = ICONS / ("eye.svg" if on else "eye-off.svg")
    label.setText(f'<a href="{key}"><img src="{icon}" width="16" height="16"></a>' if shown else "")
    label.setToolTip(("Shown on the map" if on else "Hidden") if shown else "")


def toggle_display(dialog, key):
    """A Display eye clicked: its file or Set grid shown on the map, or hidden again."""
    from qgis.core import QgsProject

    from ...qgis_bridge.display.grid import display_grids
    from ...qgis_bridge.layers.project import find_by_name

    name, on = dialog._displayed.pop(key, None), False
    if name is None:
        level = key.removeprefix("grid:")
        name = (display_grids(dialog, [level]) or [None])[0] if key.startswith("grid:") else _show_file(dialog, level)
        on = bool(name)
        if on:
            dialog._displayed[key] = name
    layer = find_by_name(name) if name else None
    node = layer and QgsProject.instance().layerTreeRoot().findLayer(layer.id())
    if node:
        node.setItemVisibilityChecked(on)
    _eye(dialog, key)


def _show_file(dialog, level):
    """The level's reference file on the map, its layer name (None when it cannot be shown). A NetCDF
    shows its first time step, read from its axes: through GDAL a long series takes minutes."""
    from qgis.core import QgsCoordinateReferenceSystem

    from ...qgis_bridge.display import canvas

    selection = dialog._resolution_files[level]
    name = f"{level} reference: {Path(selection['path']).name}"
    if Path(selection["path"]).suffix.lower() != ".nc":
        layer = canvas.show_raster(dialog, selection["path"], name)
        if layer is not None and not layer.crs().isValid():
            layer.setCrs(QgsCoordinateReferenceSystem(selection["crs"]))
        return name if layer is not None else None
    import numpy as np
    import rioxarray  # noqa: F401  (the .rio accessor)
    import xarray as xr

    from ...core.meteorology.forcing.api import netcdf_first_slice

    header = raster_grid(raster_source(selection["path"], selection["variable"]), selection["crs"],
                         selection["crs"])["header"]
    cell, (xmin, _xmax, _ymin, ymax) = float(header["cellsize"]), header_bounds(header)
    data = xr.DataArray(netcdf_first_slice(selection["path"], selection["variable"]), dims=("y", "x"), coords={
        "y": ymax - (np.arange(header["nrows"]) + 0.5) * cell, "x": xmin + (np.arange(header["ncols"]) + 0.5) * cell})
    return name if canvas.show_dataarray(dialog, data.rio.write_crs(selection["crs"]), name) is not None else None


def explain(dialog, href):
    """An ⓘ of Updated Headers: why that value differs from the file's, in a box like every info button."""
    QMessageBox.information(dialog, f"Updated header {href}", dialog._header_notes.get(href, ""))


def clear_headers(dialog):
    """Empty Updated Headers and the common extent; the file rows' info buttons go off."""
    dialog._header_notes = {}
    for level in LEVELS:
        for name, _key in (*CORNERS, ("cellSize", "cellsize")):
            getattr(dialog, f"label_{name}{level}Common").setText("")
        dialog._displayed.pop(f"grid:{level}", None)
        _eye(dialog, f"grid:{level}", shown=False)
        button = getattr(dialog, f"toolButton_description{level}File")
        button.setToolTip("")
        button.setEnabled(False)
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
    dialog._dem_replacements = {key: dict(value) for key, value in (state.get("dem_replacements") or {}).items()
                                if key in DERIVATIVES and isinstance(value, dict) and value.get("path")}
    check_dem(dialog)


def clear(dialog):
    """No setup open: no files, resolutions or grid; L0's extent ticked."""
    dialog._resolution_files, dialog._level_file_info, dialog._displayed = {}, {}, {}
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
    dialog._dem_error, dialog._dem_replacements = None, {}
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

def _fit(dialog, source, crs, factor=1):
    """`source` (in `crs`, its cells x `factor`) against the common L0 grid: its own header, its cell
    and extent in the domain CRS, the extent moved onto the L0 lattice when its cells are L0's (under
    half a cell, never resampled) and a note on that shift."""
    l0, (domain, unit) = dialog._grid_l2_metadata["l0_header"], _domain(dialog)
    own = raster_grid(source, crs, crs)["header"]
    placed = raster_grid(source, crs, domain, unit, factor)["header"]
    bounds = header_bounds(own)
    extent = Extent.from_bounds(bounds if crs_equal(crs, domain) else inner_bounds(bounds, crs, domain))
    cell, notes = float(l0["cellsize"]), []
    if math.isclose(float(placed["cellsize"]), cell, rel_tol=1e-9):
        shift = [(round(value) - value) * cell for value in ((float(placed[key]) - float(l0[key])) / cell
                                                              for key in ("xllcorner", "yllcorner"))]
        if any(abs(value) > 1e-6 * cell for value in shift):  # copy_rasters(shift=True) moves it
            notes.append(f"Shifted by ({shift[0]:.6g}, {shift[1]:.6g}) onto the L0 lattice.")
            extent = Extent(extent.xmin + shift[0], extent.xmax + shift[0],
                            extent.ymin + shift[1], extent.ymax + shift[1])
    return {"header": own, "cell": float(placed["cellsize"]), "extent": extent, "notes": notes}


def _dem_fit(dialog):
    """The DEM's fit, its extent notes (in DEM cells) and regridding; ValueError when it cannot fill
    the common L0 grid."""
    layer = dialog.input_combo("dem").currentLayer()
    if layer is None:
        raise ValueError("Select the DEM.")
    if not grid_current(dialog):
        raise ValueError("Set the resolutions first (Resolution page): the DEM must lie on the common L0 grid.")
    l0, (domain, unit) = dialog._grid_l2_metadata["l0_header"], _domain(dialog)
    data = dialog.input_combo("dem").currentData()
    crs = (data.get("source_crs", "") if isinstance(data, dict) else "") or qgis_layers.crs_string(layer.crs())
    factor = dialog._upscale_factor
    fit = _fit(dialog, dialog.input_combo("dem").source_uri() or layer.source(), crs, factor)
    cell = float(l0["cellsize"])
    if not math.isclose(fit["cell"], cell, rel_tol=1e-9):
        raise ValueError(f"The DEM x {factor} has cells of {fit['cell']:.10g} {unit}, but L0 is "
                         f"{cell:.10g} {unit}: set the upscale factor to L0 / DEM cell, or choose another L0.")
    common = Extent.from_header(l0)
    if common.intersect(fit["extent"]) is None:
        raise ValueError(f"The DEM ({', '.join(f'{v:.10g}' for v in fit['extent'].bounds)}) does not overlap "
                         f"the common extent ({', '.join(f'{v:.10g}' for v in common.bounds)}).")
    regrid = ([] if crs_equal(crs, domain) else [f"Warped {crs} → {domain}."]) + (
        [f"Topography upscaled x {factor} (mhm-tools)."] if factor > 1 else [])
    return fit, fit["notes"] + _edges(fit["extent"], common, cell / factor, 0.01 * cell), regrid


def _dem_note(dialog):
    """What happens to the DEM on the common L0 grid; ValueError when it cannot fill it."""
    edges, regrid = _dem_fit(dialog)[1:]
    return "\n".join(regrid + edges or ["Used as is."])


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


# --- DEM derivatives -----------------------------------------------------------

def show_derivative(dialog, key):
    """The derivative page for row `key`: derived from the DEM, or the Set replacement raster."""
    from ..project_tree import indicate, item

    dialog._derivative = key
    saved, box = dialog._dem_replacements.get(key, {}), dialog._replace_box
    dialog.groupBox_replaceDemDerivativesVar.setTitle(f"Replace {item(dialog, key).text(0)}")
    with QSignalBlocker(dialog.checkBox_isDerivedFromDemGrid):
        dialog.checkBox_isDerivedFromDemGrid.setChecked(not saved)
    dialog.groupBox_replaceDemDerivativesVar.setEnabled(bool(saved))
    box.domain_crs = dialog.get_crs()
    with QSignalBlocker(box.comboBox_rasterInput):
        box.comboBox_rasterInput.setCurrentText(saved.get("path", ""))
    box.read(saved.get("path", ""))
    box.comboBox_varName.setCurrentText(saved.get("variable", ""))
    if saved.get("crs") and box.mProjectionSelectionWidget_crsFileInput.isEnabled():
        box.mProjectionSelectionWidget_crsFileInput.setCrs(QgsCoordinateReferenceSystem(saved["crs"]))
    indicate(dialog, key, dialog.label_setDemDerivativesVarStatus)


def _derivative_fit(dialog, key, selection):
    """A replacement raster for `key` on the common L0 grid, its extent notes and regridding;
    ValueError when it cannot replace the DEM's: another CRS, another cell or short of the DEM."""
    fit = _dem_fit(dialog)[0]
    l0, (domain, unit) = dialog._grid_l2_metadata["l0_header"], _domain(dialog)
    name, cell = Path(selection["path"]).name, float(l0["cellsize"])
    if not crs_equal(selection["crs"], domain):
        raise ValueError(f"{name} is in {selection['crs'] or 'no CRS'}, not the domain CRS {domain}: "
                         f"reproject it first ({key.replace('_', ' ')} cannot be resampled).")
    raster = _fit(dialog, raster_source(selection["path"], selection["variable"]), selection["crs"])
    if not math.isclose(raster["cell"], cell, rel_tol=1e-9):
        raise ValueError(f"{name} has cells of {raster['cell']:.10g} {unit}, but L0 is {cell:.10g} {unit}: "
                         "it must have L0's cells.")
    common = Extent.from_header(l0)
    dem = fit["extent"].intersect(common)
    short = [line for line in _edges(raster["extent"], dem, cell, 0.01 * cell) if line.startswith("Padded")]
    if short:
        raise ValueError(f"{name} does not cover the DEM: it misses "
                         f"{short[0].removeprefix('Padded with no data: ')}")
    return raster, raster["notes"] + _edges(raster["extent"], common, cell, 0.01 * cell), []


def set_derivative(dialog):
    """The derivative page's Set: derived from the DEM (ticked), or the checked replacement raster."""
    key, box = dialog._derivative, dialog._replace_box
    previous = dialog._dem_replacements.pop(key, None)
    if not dialog.checkBox_isDerivedFromDemGrid.isChecked():
        selection = box.selection()
        try:
            if box.error:
                raise ValueError(box.error)
            _derivative_fit(dialog, key, selection)
        except (OSError, RuntimeError, ValueError) as error:
            if previous:
                dialog._dem_replacements[key] = previous
            QMessageBox.warning(dialog, "Replace DEM derivative", str(error))
            return
        dialog._dem_replacements[key] = selection
    if dialog._dem_replacements.get(key) != previous and dialog.setup_folder:
        invalidate_workflows(dialog, "execute_all_morphology")
    if "flow_direction" in dialog._dem_replacements and "flow_accumulation" not in dialog._dem_replacements:
        dialog.log_message("WARNING: Flow direction is replaced, but flow accumulation is still derived from "
                           "the DEM's flow directions: replace it too if routing needs both.")
    dialog.save_input_state()
    from .. import project_tree

    project_tree.refresh(dialog)


def _categorical_input(dialog, kind):
    """The first file of a categorical row's Set input, its variable and CRS, and how it is regridded
    onto L0 (mhm-tools, or a GDAL warp for an mHM-ready raster); None without one."""
    from .categorical import (
        categorical_source_config,
        land_use_input_value,
        uses_advanced_categorical_input,
    )

    if kind == "lai" and dialog._lai_input_config.get("input_path"):
        config = dialog._lai_input_config
        return config["input_path"], config.get("variable", ""), config.get("source_crs", ""), "Bilinear (mhm-tools)."
    if uses_advanced_categorical_input(dialog, kind) and kind == "lc":
        value = land_use_input_value(dialog._advanced_inputs["land_cover"])
        return value.as_dict()["periods"][0]["file_path"], "", value.crs, "Mode (mhm-tools)."
    if uses_advanced_categorical_input(dialog, kind):
        value = dialog._advanced_inputs["soil"]
        data = value.as_dict() if hasattr(value, "as_dict") else value
        horizon = data["horizons"][0]
        return next(path for name, path in horizon.items() if name.endswith("_layer")), "", data.get("crs", ""), \
            "Auto (mhm-tools)."
    config = categorical_source_config(dialog, kind)
    if not config:
        return None
    path = config["layers"][0][0] if config.get("layers") else config["input_path"]
    regrid = "Nearest (GDAL warp)." if "lookup_table" not in config else (
        "Mode (mhm-tools)." if kind == "lc" else "Nearest (mhm-tools).")
    return path, config.get("variable", ""), config.get("source_crs", ""), regrid


def _input_fit(dialog, key):
    """A morphology row's input: its fit, extent notes and regridding; None without a raster input."""
    if key == "dem":
        return _dem_fit(dialog)
    if key in dialog._dem_replacements:
        return _derivative_fit(dialog, key, dialog._dem_replacements[key])
    if key in DERIVATIVES:
        fit, edges, regrid = _dem_fit(dialog)
        return fit, edges, [*regrid, "Derived from the DEM (mhm-tools)."]
    found = _categorical_input(dialog, key) if key in ("lc", "soil", "geology", "lai") else None
    if not found:
        return None
    path, variable, crs, regrid = found
    if Path(str(path).split("|", 1)[0]).suffix.lower() not in (".tif", ".tiff", ".asc", ".nc"):
        return None, ["Rasterized onto the L0 grid (mhm-tools)."], ["Rasterized (mhm-tools)."]
    if not grid_current(dialog):
        raise ValueError("Set the resolutions first (Resolution page).")
    uri = raster_source(path, variable)
    fit = _fit(dialog, uri, crs or qgis_layers.crs_string(qgis_layers.crs_of(uri)))
    cell = float(dialog._grid_l2_metadata["l0_header"]["cellsize"])
    on_l0 = math.isclose(fit["cell"], cell, rel_tol=1e-9) and not fit["notes"]
    edges = _edges(fit["extent"], Extent.from_header(dialog._grid_l2_metadata["l0_header"]), cell, 0.01 * cell)
    return fit, edges, [] if on_l0 else [regrid]  # edges in L0 cells


def summary(dialog, node):
    """The morphology summary's Cell Size, nRows, nCols, Extent (the input's own grid), Extent
    Modification and Regridding for a row; "—" where it has no raster input."""
    try:
        found = _input_fit(dialog, node["key"])
    except (KeyError, IndexError, OSError, RuntimeError, ValueError) as error:
        return ("—", "—", "—", "—", str(error), "—")
    if found is None:
        return ("—",) * 6
    fit, edges, regrid = found
    header = fit["header"] if fit else None
    grid = (f"{float(header['cellsize']):.10g}", str(header["nrows"]), str(header["ncols"]),
            ", ".join(f"{value:.10g}" for value in header_bounds(header))) if header else ("—",) * 4
    return (*grid, " ".join(edges) or "None", " ".join(regrid) or "None")
