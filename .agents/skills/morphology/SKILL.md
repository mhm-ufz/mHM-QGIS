---
name: mhm-qgis-morphology
description: "The Configuration tab and input preparation: grids and the common L0/L2 extent, the v6 input layout, staging and publication, fingerprinted reuse, categorical inputs and LAI. Read before changing configuration, grids or any morphology/LAI preparation step."
---

# Configuration and morphology

Part of the mhm-qgis agent instructions; the index is in `AGENTS.md`.

## Configuration And Morphology

- Setups live at `<project>/mhm-qgis/<domain-name>`. The main dialog keeps the
  outer folder in `project_directory`; `project_folder` is the active domain
  root passed to core APIs. Never pass the outer folder to a new setup's state API.
- Browse Project populates `comboBox_domainFolderSelector` with named setups and
  `<new>` last; it does not restore inputs or scan forcing. Create Project Structure
  opens the selected setup, or asks for an unused name with `domain_folder_name.ui`,
  then opens the read-only lazy `project_folder_tree.py` dialog.
- Each domain owns its four JSON state files under `.temp/`; settings, namelists and
  outputs remain at their established locations.
  Older layouts (`Z Temp`, `data_raw`, `data/master`, `mhm-plugin`) are unsupported;
  add no compatibility code for them. Unsafe names, files and symlinks are rejected.
  Do not switch setups while background work is running.
- A new domain may clone another domain's mutable state and namelists. Prepared files
  under `data/` and `.temp/Morphology/` are relative symlinks; writers must replace
  the link itself before writing so the source domain remains unchanged. `output/` and
  `restart/` are never cloned.
- Configuration lives in `tabWidget_steps` and `stackedWidget_configuration`:
  `page_singleDomain` (work type 0) and `page_multiDomain` (work type 1, combine
  domains); the mRM-only type 2 stays disabled. Do not restore the removed steps
  stacked widget.
- **Multi-domain setups** live at `<project>/mhm-qgis-multi/<name>/` (`.temp/`, `output/`,
  `restart/` and the combined namelists only). They prepare nothing: they combine
  single-domain setups that already run on their own, and **domains are read, never
  written**. `core/multi_domain.py` is Qt-free; `qt/controllers/multi_domain.py` drives the
  page, with `dialog._multi_domains` (stored paths, relative to the multi folder) as the
  open setup's list. None means no combined setup is open. Its input state holds only
  `setup: multi`, the version, the domains and the wizard answers.
  - Adding a domain checks the setup state, the version, duplicates and a saved `mhm.nml`.
    Prepare **blocks** on fewer than two domains, different process cases, (v5) land-cover
    scenes or soil horizons, and different `geology_classdefinition.txt`; it **warns** on
    different time periods. It copies the first domain's parameter and output namelists
    into the multi folder if missing, and writes the `multi_domain` section of
    `nml-settings.json`, which switches `vSpecific` to the combined values.
  - v6: `combine.nml` (profile `meta`) lists `domain_dirs`; mHM reads each domain's own
    `mhm.nml` but takes `config_processes`, `parameters.nml` and `outputs.nml` from the multi
    folder. Run is `mhm-driver -n combine.nml`. Each domain still writes its outputs to its
    own `output_path`, inside the domain folder (prepare warns).
  - v5.13: one `mhm.nml` whose per-domain arrays (`x-fortran-shape: nDomains` in the schemas)
    come from each domain's saved namelist. Input paths (`format: directory-path` /
    `file-path`) are rebased into the domain, except `LCoverfName` (a name inside
    `dir_LCover(i)`); `dir_Out`, the restart outputs and `dirConfigOut` stay in the multi
    folder (`output/<domain>/`). Gauge rows are padded to rectangular (nml-tools rejects
    ragged arrays): the editor dimension `nGaugesTotal` is the widest row, while the value
    `nGaugesTotal` is the sum, which mHM needs for its discharge buffers.
  - Switching the work type closes an open setup of the other kind.
- `pushButton_scanPrecipitationFiles` is the explicit metadata scan boundary.
  The selected folder and relative `lineEdit_precipitationGlobDefinition` determine
  the file set, never a recursive search of every NetCDF in the project. No scan
  runs during project restoration. The worker returns metadata; the controller
  updates coverage, the multiplier default, and the persisted common grid.
- Precipitation Scan and Add/Edit Meteorology open `meteo_var_details_input.ui`.
  Only OK starts metadata inspection; Cancel leaves saved inputs unchanged. The
  variable table is populated after successful inspection and restores without scanning.
- Forcings: `pre`, `tavg`, `tmin`, `tmax`, `tann`, `pet`, `ssrd`, `strd`, `netrad`, `eabs`,
  `windspeed` (`layout.METEO_VARIABLES`, `forcing/temporal.VARIABLES`, `forcing/types`,
  `state/nml_outputs.METEO_FIELDS`, and `comboBox_meteoVar` must list the same names;
  `test_meteo_combo_offers_every_prepared_forcing` checks the form). `netrad` (W m-2) is
  what PET Priestley-Taylor (case 2) and Penman-Monteith (case 3) read, with `eabs` and
  `windspeed` for case 3; it was missing, so those cases could not be prepared.
- Each variable declares folder/glob, unit and input cadence; the form has no cumulative
  controls, so user specs are non-cumulative. Metadata must agree.
  No temperature triplet or dedicated PET workflow remains.
- Output cadence is the input cadence or a coarser hourly/daily/monthly/yearly cadence.
- Each successful meteo run saves `meteo_period` (the span every selected variable covers)
  in the processing state. Editing the simulation period inside it resets only Run mHM;
  leaving it also resets Execute All Meteo.
  Coarser output requires Average, Sum, Minimum or Maximum aggregation. Drop
  incomplete record-edge periods; reject incomplete interior periods and require
  every other variable to cover the complete precipitation window. Deaccumulation, temporal
  aggregation, unit conversion, nearest-grid alignment and padding happen only
  during execution, in that order. Cumulative sequences must be continuous across files;
  missing prefixes or ambiguous interval conventions fail rather than guessing.
  CF bounds describe intervals; recognized ERA cumulative variables use interval ends.
  Fingerprints include all options and files. Display selects actual timestamps,
  not arithmetic day offsets.
- `dateTimeEdit_simulationStartTimeInput`, `dateTimeEdit_evaluationStartTimeInput`
  and `dateTimeEdit_simulationEndTimeInput` persist under `nml-settings.json:time`.
  Core validates start <= evaluation <= end, whole dates for v5 and whole hours
  for v6, then hands periods to nml-tools. Meteorology validates input coverage.
- Scan establishes the common grid before either execution. `core/grid/raster.py`
  reads DEM metadata only (a warped VRT when CRS conversion is necessary).
- `pushButton_executeAllMorphology` schedules `EXECUTE_ALL_STAGES`: NetCDF DEM
  derivatives, saved gauge positions, morphology mask, categorical inputs, LAI,
  then one version-aware publication stage. Snapping and delineation must already
  be saved by Domain Delineator. There are no late crop, per-outlet link or
  per-outlet DEM stages.
- `pushButton_executeAllMeteo` runs only `core/executions/meteo`, never morphology.
- DEM processing does not modify mHM-tools. Derive terrain on the real DEM first, then place all
  variables once on the common L0 extent in `.temp/Morphology/dem_derivatives.nc`.
  Extract `dem_filled` as the single raster variable `dem` in `filled_dem.nc` with
  x/y/CRS metadata. Common-grid padding cannot invent sinks or flow paths.
- Derivative reuse fingerprints the DEM, CRS and exact target header. Channel and
  catchment jobs reuse saved D8 `fdir`; gauges burn their saved snapped coordinates
  directly into `idgauges.nc`. Missing filled DEMs are extracted
  from an unchanged bundle. Legacy derivative TIFFs are neither reused nor deleted.
- One model domain is the union of selected catchments; no selection means valid
  filled-DEM cells, not the padded rectangle. Cached per-outlet masks and gauge
  metadata are retained, but all active catchments and gauge memberships map to
  model domain 1. The namelist handoff contains one `master` domain.
- Publication watershed-masks **only the DEM and fdir**, into
  `dem_derivatives_masked.nc`. mHM v6 takes fdir's valid cells as its L0 grid and
  does not narrow it by `morph_mask`, so an unmasked fdir runs MPR over the whole DEM
  extent. v5 writes both `.asc` files masked (fdir matching `dem.asc`); v6 keeps the
  full fdir in `input.nc` and writes the masked one to `data/morph/fdir.nc` (variable
  `fdir`, written like `input.nc`: projected axes and 2-D lat/lon, which mHM reads with
  the grid), which `register_morphology` registers as `fdir_path`. Slope, aspect, facc and idgauges go
  into `input.nc` (v6) or the ASCII set (v5) straight from `dem_derivatives.nc`;
  `setup._model_sources()` is the one place that composes the two bundles, and
  `write_morph_mask` still reads the mask off the masked DEM's nodata.
  Soil, geology and land cover keep full class coverage (fill value 1); LAI keeps
  full coverage with fill value 0. They are never watershed-masked.
- v5 writes the final master `dem.asc`, slope, aspect, facc, fdir and optional
  `idgauges.asc`; v6 writes master `input.nc` and a single-variable `dem.nc`.
  v6 also writes `data/morph/morph_mask.nc` with `mask`, `lat` and `lon`, and registers
  prepared paths/variable names in `nml-settings.json` for nml-tools.
  There is no per-outlet output tree or domain-link step. Existing old folders are
  left untouched. Writers atomically replace any old destination symlink.

## Common L0/L2 Extent

- `core/grid/` owns exact resolutions, cell-edge extents, headers, integer factors,
  alignment gaps, coordinate axes and array matching. Its local `Resolution` does
  no file I/O or rounding; `Grid` adds per-level extents and one common extent.
  Core accepts plain values/paths/CRS strings, never widgets or QGIS objects.
- The common extent covers the selected DEM extent, not selected catchment bounds.
  Snap it outward to an L0-aligned L2 lattice. Derive L0 from L2 (same corners,
  `ncols*n`, `nrows*n`) so n x n cells per L2 cell holds by construction.
  `validate_l0_l2_alignment()` checks every header pair.
- The exact raw L2/L0 integer factor is a spin-box default only. Once the user
  chooses a multiplier it wins, including after rescans or project restoration.
- Compare cell-edge origins when measuring alignment. `settings.yaml`'s
  `grid_alignment_gap_limit` is a fraction of exact L0 cell size; gaps larger than
  numerical noise resample L2, never shift prepared L0. Expand boundaries outward
  only; never shrink a misaligned boundary.
- Never round a grid-defining cell size. 1/1200 degree and 120 times that value
  must be carried verbatim: display rounding can introduce cumulative drift or
  make an exact 0.1-degree ratio fail validation. `floor_cellsize`/`ceil_cellsize`
  are display-only. Persist both exact headers and the multiplier in processing
  state's `grid` section; restore those, not rounded widget text.
- Grid validity includes DEM file fingerprint, CRS, precipitation selection/files,
  multiplier and gap setting. Changing any requires a new scan. Changing catchment
  selection invalidates morphology, not the DEM-wide common extent.
- Prepared L0 rasters use integer window copies and padding, never resampling.
  `crop_aligned_l0_raster()`/`mask_aligned_l0_raster()` remain low-level APIs and
  reject a misaligned or wrong-CRS source; they are not a late crop workflow.
- Categorical and LAI staging outputs must already match the exact common L0 grid.
  Publication validates and copies them without a second crop, resample or invented
  class-1 fill. Only the v5 DEM is watershed-masked; v6 keeps `dem.nc` whole and
  uses `morph_mask.nc` to define active cells.
- ASCII padding/filling streams rows (`handlers/file/ascii/pad.py`). Do not load
  huge class rasters into memory to pad them. Only class products are published;
  original soil horizon inputs are never rewritten.
- Publication is one `execute_all_morphology_publish` boundary. It emits v5 ASCII
  inputs or v6 NetCDF inputs and always publishes `idgauges.asc` separately when
  `idgauges.nc` exists; `idgauges` never belongs in v6 `input.nc`.
- Aligned L2 forcing is sliced/padded by `core/grid/arrays.py`; only misaligned or
  reprojected forcing is resampled (nearest-neighbour). Check every output against
  the saved L2 header before publication.
- Generic forcing streams one selected variable in bounded time blocks, retaining
  only one preceding cumulative plane and one output-interval accumulator across files.
- Meteorology reuse checks both the input-file/selection fingerprint and target L2
  grid, as well as output existence. PET is required only when selected.
- Model inputs live under each named domain’s `data/`. v5 morphology is `data/static/morph`, forcing `data/meteo`,
  LAI `data/lai`, lat/lon `data/latlon.nc`, observations
  `data/observation/streamflow`. See version differences below.

## v6 Input Layout

- `store/layout.project_version()` reads `mhm_version` from
  `mhm_qgis_input_state.json`, so the path helpers branch on version internally and the
  ~24 callers of `morph_folder` / `lai_folder` never thread it through. The result is
  cached per project folder against the state file's mtime -- it used to re-parse the
  JSON on every call, inside loops. Unknown or missing version falls back to v5.13, which
  every existing project already uses. The shared v5.13 layout stays the same; domain DEMs now live in master.
- `ensure_project_structure()` honours its own `version_text` argument. It used to read
  the version from state instead, so on a *new* project the state file did not exist, the
  v5.13 default won, and asking for v6 still built `data/static/morph`.
- v6 differences, all matching `.scripts/project-template/v6` and the mHM examples:
  - `data/morph/` instead of `data/static/morph/`
  - one `input.nc` instead of separate `.asc` -- `core/morphology/layers/morph_input_nc.py`
    bundles the masked L0 rasters with `x`/`y`/`x_bnds`/`y_bnds` and 2-D `lon`/`lat`, so
    separate `data/latlon.nc` supplies all four model levels for v6 too
  - `data/meteo/<var>.nc`, flat, no `header.txt`, plus `mask.nc` from
    `core/meteorology/mask.py`
  - `data/morph/dem.nc` as the single model DEM; other static inputs live in `input.nc`
  - `data/gauge/streamflow/` instead of `observation/streamflow/`
- `input.nc` carries `dem, fdir, slope, aspect, geology_class, soil_class, facc`
  and optional `LAI_class`. The LAI class is present only when the categorical
  `mHM ready` or lookup workflow produced it; gridded `lai.nc` remains separate.
- The meteorology reuse check has no header file to compare under v6, so it validates the
  written NetCDF grid against the saved L2 header instead.

## Staging And Publication

- Execute All formats inputs on the already established common filled-DEM grid.
  Lookup, advanced and mHM-ready categorical modes all stage under
  `.temp/Morphology/morph`, then publish during Execute All's final stage.
- Multi-horizon soil always stages as `soil_horizon_class.nc` in database mode 1.
  Both versions keep `soil_classdefinition_iFlag_soilDB_1.txt`; v5 publishes one
  `soil_class_horizon_XX.asc` per horizon while v6 keeps the NetCDF.
  Its `nSoilHorizons_mHM` and `soil_Depth` contain exactly the configured horizons.
- `advanced_l0.publish_model_inputs()` validates exact alignment, fills class
  nodata, and copies into `data/`. It retains staged rasters and definitions for
  reuse. Ready TIFFs and v5 NetCDF class rasters are exported to ASCII by streaming
  rows, without interpolation. Advanced v6 NetCDF products remain NetCDF.
- Publication rewrites `output_path`, `classdefinition_path` and historical scene
  paths in `nml-settings.json` to the final files, including converted extensions.
  It fails if the handoff names missing products. No namelist may point at staging
  after successful publication.
- The final derivative write follows category publication; v6 can therefore bundle
  the published soil/geology classes into `input.nc`.
- Publication never copies a published file back into staging. It used to, which
  left a stale ASCII copy of every class grid in `.temp/Morphology/morph`, and every
  run then exported those grids twice: first from the copy, then from the NetCDF.
  A staged non-NetCDF raster whose ASCII name a staged NetCDF also produces is
  skipped with a log line; the NetCDF is the product.
- Every ASCII export (`write_domain_dem_ascii`) is reused when its source, reference,
  mask and options are unchanged: publication passes `project_folder`, and the
  fingerprint is recorded per output path in the `exports` section. An output that is
  a symlink (a cloned domain's link into its source) is always rewritten. On
  Karnali-v5 an unchanged re-publication fell from about 57 s to 0.1 s.
- The ASCII text is a contract: each cell is `str(int(v))` when whole, else Python's
  shortest `repr`. `_ascii_rows` formats a whole-number row through int64, about 2x
  faster, and any other row per cell with `repr`. Converting a double to its shortest
  text costs about 0.5 us whatever does it: numpy's `astype(str)` is identical but
  slower than `repr`, so do not vectorize that part. `cmp` the bytes of every export
  before and after touching this writer.
- A job made of several steps reports through `TaskShare` (`handlers/raster/tasks.py`),
  so each step fills its own share of one progress bar instead of restarting it:
  publication's class grids, LAI and morphology grids share one bar, and the ASCII
  writer reports once per 64-row block, not once per row.

## Fingerprinted Reuse

- `core/handlers/state/cache.py` records, in `mhm_qgis_processing_state.json`, a
  fingerprint of each stage's inputs next to the outputs it produced. A stage reruns only
  when an input's size or modification time changed, its configuration changed, or one of
  its recorded outputs is gone. Content is never hashed, so the check stays cheap on
  multi-gigabyte inputs.
- Sections in use: `stages` (per Execute All stage, from
  `qt/workflows/morphology.py`), `exports` (per published ASCII file, from
  `write_domain_dem_ascii`) and `meteo_inspection` (per meteorology folder, from
  `core/meteorology/forcing/cache.py`). Keep
  entries additive; `store_payload` merges into the existing state rather than replacing.
- The categorical lookup stages and the LAI stage are gated on it. Geology used to rerun
  every time because only land cover and soil passed `reuse_existing`, and that check was
  existence-only rather than input-based. Advanced land cover and soil fingerprint all selected source files, the
  filled DEM and formatting options through their core API.
- Outputs already on disk with no recorded fingerprint are **adopted** when they postdate
  every input (`outputs_newer_than_inputs`), only when that stage has no recorded fingerprint, so changed configuration
  cannot adopt its own stale outputs. An output older than an input is certainly stale
  and is rebuilt instead.
- `core/meteorology/forcing/cache.py` wraps `inspect_meteo_folder`, which opens every
  NetCDF in a folder **twice**. On a 552-file ERA5-Land record that froze project load for
  ~22 s per folder; reusing an unchanged inspection brings the whole `load_input_state`
  from 22.6 s to 0.26 s. Do not call `inspect_meteo_folder` directly from the dialog.
- That cache has **two** digests, and they are not interchangeable.
  `inspection_fingerprint` covers only what inspection reads (files, kind, source,
  CRS, pattern, input and output cadence, unit, cumulative settings); it deliberately
  omits aggregation, which must not re-open 4x552x2 files in the worker. `preparation_fingerprint`
  covers the whole spec and is what `executions/meteo._input_digest` uses, so a
  changed aggregation still rebuilds the forcing.
- Forcing preparation covers complete periods within the precipitation record,
  not only the simulation period; `run.times` only validates coverage. Both v5
  and v6 write `time` and `time_bnds` in hours since 1900-01-01.
- Output interval keys come from `temporal.output_periods`, one vectorised
  `datetime64` truncation per chunk, with `period_bounds` giving the interval's
  start and end. `datetime64` units are calendar units, so `[M] + 1` and
  `[Y] + 1` do the 28/29/30/31-day and leap arithmetic that the removed
  `output_period` used `pd.DateOffset` for -- that call was per timestep, fell
  through to `dateutil.relativedelta`, and cost ~38 us a step. Do not put
  pandas back on that path.
- Intervals are reduced with numpy, never a per-timestep Python loop.
  `_group_edges` finds each run of equal keys, `_reduce_groups` reduces them all
  in one reshaped call when their lengths are uniform (the hourly-to-daily case)
  and falls back to one vectorised reduction per interval when they are not
  (monthly, yearly). `_reduce_axis` uses `fmin`/`fmax`, which ignore NaN, so
  Minimum/Maximum need no masked copy of the block. `_merge_group` folds the one
  interval that straddles a file boundary. The read block is sized only by the
  64 MiB budget: the old hard cap of 32 steps split every month file into 24
  chunks and made whole-interval reduction impossible.
- Aggregated intervals are stacked into batches of `api.INTERVAL_BATCH` before
  they are aligned and written, and the output NetCDF is opened **once** per
  variable. Doing either per output interval meant one resample, one Dataset
  build and one file append per day, and the append cost grew with the unlimited
  time dimension. `_crs_description` is memoized for the same reason: pyproj was
  rebuilding a constant CF grid mapping on a Qt worker thread per block.
- `process_meteo_inputs` takes `progress=`, ticked once per input file. Execute All
  Meteo is an ordinary `TaskCoordinator` job (`qt/workflows/meteorology.py`): it
  passes `task.setProgress` as `progress`, `task.isCanceled` as `cancelled`, and logs
  through `coordinator.log`. It used to run on its own `QThread` beside the
  coordinator, which needed a second cancel/close path and appended to the log
  widget from the worker thread.

## Categorical Morphology Inputs

- The DEM and pour-point combos are wrapped in `InputComboAdapter`
  (`qt/dialogs/input_selection.py`). Loaded QGIS layers are always listed; folder
  searching (disabled in the form) would add relative project paths. The adapter opens a
  file-backed layer once per file version, not on every `currentLayer()` call. The
  precipitation folder combo never scans the project: it holds a blank entry plus the
  browsed or saved folder.
- A categorical source lives only in the dialog's config dicts
  (`_categorical_lookup_configs`, `_categorical_ready_configs`, `_lai_input_config`,
  `_advanced_inputs`), written when its child dialog is accepted. The main form has no
  land cover, soil, geology or LAI layer combo; four hidden stand-in combos used to
  duplicate that record. A lookup job recovers a loaded QGIS layer with
  `qgis_bridge.layers.find_by_source(input_path)` when it must materialize one.
- Land cover, soil and geology use either `mHM_ready` or `Lookup Table` mode. Lookup mode
  stores an explicit CSV or delimited TXT lookup file, mapping field and class field.
- **All lookup-table reading goes through `core/handlers/lookup`.** `table.read` reads
  `.csv` via mHM-tools and delimited `.txt` via pandas; `table.normalize_key` is the one
  place that folds a column name (dropping a leading `*` and a `[unit]` suffix), and
  every name comparison must use it. `table.columns` lists the selectable fields, hiding
  starred and geometry columns. `table.as_csv` is a context manager that hands mHM-tools
  a real CSV, converting a TXT lookup into a scratch directory it owns -- it must not
  write into the directory it is handed, because one caller passes a real output folder.
- `mhm_tools.pre.rasterize_map_data`, `format_soil_data`, `format_geology_data` and
  `format_lc_data` perform all categorical raster computation. Do not restore local QGIS
  reclassification or guessed mappings.
- Vector lookup processing burns the selected class field. Its follow-up formatter must
  therefore map `class_field` to itself; do not map the original source field twice.
- The filled DEM defines the exact target grid and CRS. Original input and DEM CRS
  arguments are missing-metadata fallbacks and must not override conflicting embedded
  metadata.
- Lookup mode stages `lc.nc`, `soil_class.nc` and `geology_class.nc` under
  `.temp/Morphology/morph`. `externals/mhm_tools_handler._OUTPUTS` is the single
  statement of that format: the raster name is handed to mhm-tools as its `-e`
  extension, `categorical_output_suffix(kind)` is what callers name the file
  with, and `prepare_categorical_file` refuses an output name whose suffix
  disagrees. A GeoTIFF named `.asc` once reached publication as a text grid and
  failed there with a `UnicodeDecodeError` instead of failing at the boundary.
  v6 publishes those NetCDFs as they are and `write()` merges them into
  `data/morph/input.nc`; v5.13 converts them to ASCII at publication.
- Lookup mode retains `3_land_use.tif`, `3_soil.tif` and `3_geology_processed.tif` for
  the existing crop/mask/write workflow.
- Soil and geology classdefinitions are produced once by their formatter and moved to
  `data/static/morph`. Only geology `PARAMETER_VALUE` metadata remains
  plugin-specific, written by `core/handlers/lookup/geology.py` to
  `.temp/Morphology/geology_class_metadata.json`.
- `mHM_ready` accepts local `.asc`, `.nc` or `.tif` rasters, copies them to
  `data/static/morph`, and bypasses categorical crop/mask/write. Soil and geology require
  their existing classdefinition in that folder; geology also requires its plugin
  metadata in `.temp/Morphology`.
- Prefer direct local paths. Materialize provider sublayers and non-file-backed QGIS
  layers only at the plugin boundary.
- Do not add Shapely validity checks or `make_valid` calls before rasterizing.
- The `format-data.csv` manifests for historical land cover and layered soil are built by
  `core/handlers/lookup/manifests.py`, which validates before writing: land-use periods
  use continuous `StartDateTime,EndDateTime,FilePath` records (ISO timestamps or
  year boundaries), bounded by the simulation interval. UI QDateTimeEdit cells share
  adjacent endpoints; saved periods outside the simulation interval open clamped, with a
  red note in the dialog, never a refusal (the dialog is the only place to fix them); v5 transitions must be January 1. Soil horizons are contiguous
  starting at depth 0. The approved small mhm-tools `format_lc_data.py` extension
  reads these dates and preserves fractional-day NetCDF time bounds; legacy
  `StartYear/EndYear` files retain inclusive-year meaning. **Their bytes are a contract mHM-tools parses** -- the soil unit field
  (the repeated `Bulk Density Unit` column) and relative-path rewriting are part of it. Diff the
  output bytes before and after any change here.

## LAI

- The implemented LAI workflow is input type item 0: long-term mean monthly gridded data
  from NetCDF.
- For NetCDF LAI, disable lookup table and lookup field controls.
- Infer the input cadence from the NetCDF time coordinate; do not ask the user to repeat
  it in the UI.
- Clip/resample LAI to the filled DEM L0 grid. The filled DEM may be projected while LAI
  may be lat/lon.
- **Execute All has one plan.** `pushButton_executeAllMorphology` goes to
  `MorphologyTaskBridge.start_execute_all()`, which schedules
  `core/executions/morphology.EXECUTE_ALL_STAGES`. Every user-visible execution command,
  including snapping and gauge-position creation, must be represented in that API plan;
  do not create a synchronous twin.
- The Execute All Rasterio warp fills cells the source cannot supply with **0**: a
  missing leaf area is none, not a gap. The later aligned window copy pads beyond the
  staged extent with the same **0**. LAI is not watershed-masked, so `-9999` should not
  appear in a published LAI cube.
- Execute All resamples LAI onto the common filled-DEM grid and stages
  `.temp/Morphology/lai/lai_dem.nc`. Its publication step window-copies this to
  `lai.nc` with zero filling and no watershed mask. There is no `lai_crop.nc`.
  A changed common grid invalidates staged LAI through the filled-DEM fingerprint.
- Reusable temporal conversion, Rasterio warping, streaming NetCDF writing and aligned
  window copying live in `mhm_tools.pre.format_lai`. Call that public API only through
  `externals/mhm_tools_handler.py`. `core/morphology/layers/lai.py` builds path-only
  task options; the Qt scheduler snapshots widgets before the worker starts. Never touch
  a widget from the worker.
- mHM-tools streams every stage one block of target rows and one time step at a time.
  Never materialise the whole target cube or the full WGS84 target mesh.
- `lai_window_offsets()` (in mhm-tools) validates cell size and alignment before
  copying and raises rather than silently shifting the data.
- Target WGS84 coordinates are generated by row block rather than as a full mesh;
  projected grids must remain memory-bounded too.
- The LAI variable is written at zlib level 1 on purpose: level 4 compresses an upsampled
  grid no better (78:1 vs 76:1) and takes nearly twice as long.
- Bilinear output barely compresses. Measured on real GIMMS LAI upsampled 100x:
  nearest-neighbour reached 70:1 on its runs of repeated values, bilinear only 1.80:1 on
  the DEM grid and 1.85:1 on the L0 grid because every cell differs. That is why mhm-tools'
  `LAI_ASSUMED_COMPRESSION` is 1.8, and why a long record at L0 resolution is expensive
  on disk: 468 monthly steps on a 13320 x 6120 grid is ~158 GiB per file, and the
  pipeline retains two (`lai_dem.nc`, `lai.nc`). Check the numbers before
  assuming a long record is feasible; the 12-step long-term-mean option is ~4 GiB per file.
- `lai.nc` is the published file; `lai_masked.nc` is not written, because a second
  full-size copy costs disk for no benefit.
- `mhm_tools.pre.format_lai.assert_lai_output_fits()` is a disk check, not a memory
  check: memory is bounded by the streaming writer. It compares the estimate against
  real free space via `shutil.disk_usage` and raises `MemoryError`. There is **no**
  environment-variable override -- an earlier version of this document claimed
  `MHM_TOOLS_LAI_MAX_BYTES`, which does not exist.
- LAI is a 3D monthly array: 12 months plus spatial dimensions. Crop operations must
  apply to all months. Output NetCDF must be double precision (`float64`). Write the L0
  header for LAI to `data/lai/header.txt`.
