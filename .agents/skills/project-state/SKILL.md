---
name: mhm-qgis-project-state
description: "Setup state files, domain delineation settings and the fixed output paths. Read before reading or writing setup state, settings or output paths."
---

# Project state and paths

Part of the mhm-qgis agent instructions; the index is in `AGENTS.md`.

## State Files

Four JSON files live under each domain's `.temp/` directory.

- `mhm_qgis_input_state.json`: UI state. `store/layout.project_version()` reads
  `mhm_version` from it.
- `mhm_qgis_domain_delineation_state.json`: canonical detailed delineation state
  (`state/domain_state.py`).
- `nml-settings.json`: the normalized handoff to nml-tools -- domain directories, DEM
  paths, contained gauge IDs, shared `data/` paths (`state/nml_settings.py`).
  Despite the name it has nothing to do with the external `nml_tools` package.
- `mhm_qgis_processing_state.json`: output registry, workflows, grid contract, domains
  and reuse fingerprints. Every update goes through
  `core/handlers/state/processing.py`, whose locked read-modify-write operations preserve
  unrelated sections. `state/cache.py`, `state/meteo_outputs.py` and
  `store/registry.py` are focused facades over that boundary; never dump a stale
  in-memory state copy. `tests/test_shared_processing_state.py` covers parallel and
  cross-feature updates.

## Domain Delineator And Project Settings

- `core/handlers/state/settings.py` copies packaged defaults to the active domain's
  `settings.yaml` once, without overwriting it. Opening a named domain reloads and
  validates it; snapping and grid preparation read that domain’s values.
- `max_snap_buffer_distance` is the single snapping radius, in metres (default 1000).
  `core/grid/distance.py` supplies local distance CRS/units, including latitude-aware
  geographic conversion and projected feet. QGIS geometry/index work stays in
  `qgis_bridge/layers/domain.py`.
- Count every channel feature within the radius, then prefer highest stream order,
  breaking ties by distance. No candidates means nearest actual geometry with no
  distance cutoff. Confidence is 0/red for no candidates, 1/yellow for multiple, and
  2/green for one. Manual picks have no automatic confidence. Shapefile fields use
  `confidence`, `snap_count`, `snap_dist` (metres), `snap_state`, and `snap_order`.
- Next, list selection and map picks never start delineation. Show Delineation runs
  `core/executions/morphology/delineation.show` through the task coordinator. A valid
  saved mask displays immediately on selection; changed DEM/coordinates or a missing
  file invalidate reuse. `domain_state` persists nested `delineation` cache metadata
  without committing draft domain/gauge choices.
- Save uses `delineation.save` to prepare **every listed outlet**, reusing valid masks,
  and merges selected catchments into one domain, or valid DEM cells when none are selected. Cache `mask_value`
  is independent of reassigned `domain_id`. Save commits mask paths, choices, gauges,
  and the namelist handoff; the final master DEM is written by Execute All.

## Output Paths To Remember

- Temporary morphology: `.temp/Morphology`
- Morphology staging: `.temp/Morphology/morph`
- Shared static morphology: `data/static/morph` (v6: `data/morph`)
- Single-domain DEM: `data/static/morph/dem.asc` (v6: `data/morph/dem.nc`)
- LAI: `data/lai` (v6: `data/morph`)
- Meteorology: `data/meteo`
- Streamflow: `data/observation/streamflow` (v6: `data/gauge/streamflow`)
- Root namelists: active domain root (`mhm.nml`, parameter namelist, `mhm_outputs.nml`)
- mHM outputs: `output`
- Restarts: `restart`
