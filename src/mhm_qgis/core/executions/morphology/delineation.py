"""Path-only Show/Save commands; reuse outlet masks independently of domain IDs."""
from pathlib import Path

from ...handlers.raster import tasks as raster
from ...handlers.state.domain_state import cached_delineation, delineation_fingerprint


def domain_mask(task, project_folder, filled_dem, crs):
    """Rebuild one model mask from saved catchments, or valid DEM cells."""
    from pyproj import Transformer
    from ...handlers.state import domain_state, nml_settings
    from ...handlers.store.layout import MERGED_MASK_NAME, domain_dem_path
    from ...handlers.store.paths import geometry_folder

    state = domain_state.load_state(project_folder)
    domain_state.assign_domain_ids(state)
    folder = Path(geometry_folder(project_folder)) / "Watersheds"
    folder.mkdir(parents=True, exist_ok=True)
    outlets = []
    for index, (outlet_id, record) in enumerate(state["outlets"].items(), 1):
        if not record.get("is_domain"):
            continue
        picked = record.get("picked") or record.get("delineation", {}).get("picked")
        if not picked:
            raise ValueError(f"Save the location of catchment {outlet_id} in the delineator first.")
        x, y = float(picked["x"]), float(picked["y"])
        if picked.get("crs") and picked["crs"] != crs:
            x, y = Transformer.from_crs(picked["crs"], crs, always_xy=True).transform(x, y)
        outlets.append(dict(outlet_id=outlet_id, x=x, y=y, picked=picked,
                            basin_id=index, domain_id=1,
                            raster_path=str(folder / f"catchment_{index}.tif"),
                            cached=record.get("delineation"),
                            **{key: record.get(key, record.get("delineation", {}).get(key))
                               for key in ("confidence", "snap_count", "snap_distance_m")}))
    dem_domain = None if outlets else (1, str(folder / "4_watershed_DEM.tif"),
                                       domain_dem_path(project_folder, "master"))
    result = save(task, project_folder=project_folder, filled_dem=filled_dem,
                  outlets=outlets, merged_path=str(folder / MERGED_MASK_NAME), dem_domain=dem_domain)
    for outlet_id, item in result["outlets"].items():
        state["outlets"][outlet_id].update(delineation=item, mask_path=item["raster_path"])
    state["merged_mask_path"] = result["merged_path"]
    state["dem_mask_path"] = result.get("dem_mask_path") or ""
    domain_state.save_state(project_folder, state)
    nml_settings.sync_domain_settings(project_folder)
    from ...handlers.state import processing
    from ...handlers.store.layout import morph_folder
    processing.save_domains(project_folder, [dict(
        domain_id=1, outlet_id="", name="master", mask=result["merged_path"],
        directory=morph_folder(project_folder), dem_path=domain_dem_path(project_folder, "master"))])
    return result["merged_path"]


def _outlet(project_folder, filled_dem, outlet, context, task):
    raster._cancelled(task)
    x, y = outlet["x"], outlet["y"]
    result = cached_delineation(project_folder, filled_dem, x, y, outlet.get("cached"))
    if result is None:
        if context is None:
            context = raster._flow_context(filled_dem)
        digest = delineation_fingerprint(filled_dem, x, y)
        path = Path(outlet["raster_path"])
        # A preview must not overwrite the last committed domain mask.
        path = path.with_name(f"{path.stem}_{digest[:16]}{path.suffix}")
        result = raster._delineate(context, x, y, str(path), outlet["basin_id"])
        result.pop("mask")
        result.update(fingerprint=digest, mask_value=outlet["basin_id"])
    result["picked"] = dict(outlet["picked"])
    for key in ("confidence", "snap_count", "snap_distance_m"):
        result[key] = outlet.get(key)
    return result, context


def show(task, *, project_folder, filled_dem, outlet):
    """Delineate only on explicit request, or return a valid cached mask."""
    result, _context = _outlet(project_folder, filled_dem, outlet, None, task)
    raster._progress(task, 100)
    return result


def save(task, *, project_folder, filled_dem, outlets, merged_path, dem_domain=None):
    """Prepare every listed outlet, merging only the active domain masks."""
    context = None
    results, masks = {}, []
    for index, outlet in enumerate(outlets):
        result, context = _outlet(project_folder, filled_dem, outlet, context, task)
        results[outlet["outlet_id"]] = result
        if outlet.get("domain_id"):
            masks.append((outlet["domain_id"], result["raster_path"]))
        raster._progress(task, 80 * (index + 1) / max(1, len(outlets)))
    context = None  # Release the flow grid before reading masks for the merge.
    dem_mask = None
    if dem_domain:
        raster._cancelled(task)
        domain_id, dem_mask, _dem_path = dem_domain
        # DEM extent needs only its valid-cell mask, not a flow-grid calculation.
        reference = raster._read_raster(filled_dem, as_float=True)
        _dem, invalid = raster.pyflwdir_handler.normalise_dem(reference["array"], reference["nodata"])
        raster._dem_domain({"reference": reference, "invalid": invalid}, domain_id, dem_mask)
        masks.append((domain_id, dem_mask))
        del reference, _dem, invalid
    raster._cancelled(task)
    merged = raster.merge_domain_masks(masks, merged_path)
    raster._progress(task, 100)
    return {"outlets": results, "merged_path": merged, "dem_mask_path": dem_mask,
            "dem_domain_path": dem_domain[2] if dem_domain else None}
