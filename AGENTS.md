# AGENTS.md

Guidance for coding agents working on `mhm_qgis`: a plugin targeting QGIS 3/Qt5 and QGIS 4/Qt6
that prepares mHM inputs and namelists. The GUI requires real QGIS libraries,
whether hosted by QGIS Desktop or launched directly.

## Working Style

- **Invoke the `ponytail` skill on every coding task here** -- writing, fixing,
  refactoring, reviewing or choosing a dependency. Shortest working diff,
  deletion over addition, stdlib before a new helper, and no abstraction with
  one caller. It is the house style, not an option.
- Keep code and schema comments to what is not obvious from the names. The
  reasoning belongs in this file, where it is read once rather than in every
  diff.

## Repository Shape

- `src/mhm_qgis/`: the QGIS plugin folder **and** the Python package. Both at once --
  QGIS loads this directory directly, and it is what the wheel ships.
- `src/mhm_qgis/metadata.txt`, `__init__.py`, `mhm_qgis.py`: QGIS plugin entry points.
- `src/mhm_qgis/qt/__main__.py`: `mhm-qgis` console entry point and
  `python -m mhm_qgis.qt`; initializes a real `QgsApplication` without Desktop.
  QGIS Python/runtime and Processing libraries must already be installed.
- `src/mhm_qgis/nml-schemas/`: versioned by mHM version, currently `v5.13` and `v6`.
- `setup.py`, `pyproject.toml`, `MANIFEST.in`: PyPI packaging metadata.
- `tests/`: core checks run in plain Python; GUI checks require real QGIS and
  are skipped when unavailable. `conftest.py` initializes an offscreen QGIS application.

A local QGIS install typically symlinks the plugin at
`/usr/share/qgis/python/plugins/mhm_qgis -> src/mhm_qgis`, which is why a real-QGIS
smoke test exercises the working tree rather than a copy. Check before relying on it.

## Package Layout

Paths in this section and below are relative to `src/mhm_qgis/` unless they start with
`src/`, `tests/`, or a project-runtime folder (`data/`, `.temp/`, `output/`).

Each package is an API with one job. The point of the split is that a change has exactly
one obvious home; working out which package it belongs in is usually the whole design
decision.

- `qt/`: everything Qt, split by role -- see Qt Layering.
- `qgis_bridge/`: drives the QGIS application itself.
  - `display/`: puts a prepared raster on the map canvas (`canvas`, `morphology`,
    `meteo`, `output`).
  - `layers/`: the only place that knows the `QgsRasterLayer` / `QgsVectorLayer` /
    `QgsProject` vocabulary -- `loader` (build + validate), `project` (register, find,
    remove), `crs` (`crs_of`, `transform_to_raster`), `compat` (QGIS version shims).
- `core/`: computes without knowing Qt or QGIS exist, which is what makes it testable.
  - `native_worker.py`: Qt/QGIS-free entry point for disposable raster subprocesses,
    launched as `python -m mhm_qgis.core.native_worker`.
  - `handlers/file/json/`: atomic JSON read/write (`read`, `write`, `merge_sections`).
  - `handlers/file/netcdf/`: grid variables, time axes, slices; `raster.py` owns
    exact, windowed NetCDF raster copies with GDAL-readable grid/CRS metadata.
  - `handlers/state/`: state persistence -- `cache` (fingerprints), `nml_settings`,
    `domain_state`, `settings`, `meteo_outputs`; `processing` is the single update boundary for
    `mhm_qgis_processing_state.json`.
  - `handlers/store/`: where a file is and whether it exists yet -- `paths` (pure
    joins), `layout` (version branching, project structure), `registry` (what exists),
    `morphology` (prepared-output discovery and historical-period selection).
  - `handlers/lookup/`: CSV/TXT lookup tables and the manifests built from them --
    `table`, `manifests`, `geology`, `job`.
  - `post/`: mHM output discovery and variable/time resolution. `graph/` resolves
    saved gauges and Qt-free discharge series; the pyqtgraph window lives in `qt/dialogs/`.
  - `utils/`: dependency-free leaf helpers (`time`).
  - `grid/`: QGIS-free exact resolutions, extents, headers, alignment, coordinate
    generation and NumPy/xarray grid matching.
  - `morphology/`: QGIS-free DEM/hydrology, categorical, LAI, domain-DEM and
    elevation-band operations. `layers/advanced_inputs.py` prepares historical land
    cover and layered soil in staging; `layers/advanced_l0.py` handles final common-L0
    alignment and publication.
  - `meteorology/`: QGIS-free forcing preparation; `forcing/` owns independent
    variables and native-cadence streaming. `forcing/temporal.py` validates units,
    time axes and cumulative reset intervals. Existing NetCDF writers are reused.
  - `executions/`: canonical workflow plans and commands. `executions/morphology/`
    is the sole definition of Execute All and its publication ordering;
    `executions/observations/` reuses the forcing pipeline for L2 gridded observations.
- `externals/`: one handler per external package or executable. Processing calls into
  `mhm_tools`, `nml_tools` and `pyflwdir` go through `mhm_tools_handler.py`,
  `nml_tools_handler.py` and `pyflwdir_handler.py`; `mhm_handler.py` resolves the
  installed `mhm==5.13.4` command for v5 and validates the user-selected v6
  executable. Heavy imports stay inside functions.
- `vSpecific/`: version-specific initial values handed to the namelist editor.

Top-level modules that remain include `mhm_qgis.py` (plugin lifecycle, reload-safe
imports) and `dependency_bootstrap.py`. Main-form configuration actions live in
`qt/controllers/main.py`; morphology task scheduling lives in
`qt/objects/morphology_tasks.py`.

## Qt Layering

A module belongs to exactly one layer.

- `qt/ui/forms/`: Qt Designer source, one `.ui` per dialog.
- `qt/ui/__init__.py`: one `load_form` helper using `qgis.PyQt.uic.loadUiType`.
  Forms are the runtime source of truth: no `pyui/`, `pyuic5` or `pyrcc5` step.
  Icons use paths relative to the form; promoted-widget headers use `qgis.gui`.
  Keep XML enum spellings compatible with Qt5 Designer (e.g. `Qt::Vertical`);
  the runtime loader generates the binding-specific Python. `resources.qrc` is
  retained only for Designer asset editing, never imported at runtime.
- `qt/bindings/`: connects a signal to a slot and nothing else.
- `qt/controllers/`: what a widget shows and whether it is enabled -- reads and writes
  widget values, populates combos, validates and warns.
- `qt/dialogs/`: the dialog classes themselves, one module per form.
  `MhmQgisDialog` owns its logging and prerequisite checks directly.
- `qt/threads/`: `QThread` workers, `task_coordinator.py` (real QgsTask scheduling). The native raster subprocess entry point belongs to `core/`,
  not this Qt package.
- `qt/objects/`: a long-lived `QObject` attached to a widget rather than defined by one
  -- `viewport_raster_range.py` watches a map canvas and keeps a renderer in step. Not
  keyed to a single form, so it belongs in none of the three layers above.

Rules that follow from it:

- **One `bindings` and one `controllers` module per form, named after the form.** Two
  exceptions, both because two forms share one implementation class rather than because
  the rule bends: `discharge_assignment.py` covers the two discharge forms, and the
  land-use/soil dialogs share `_DynamicInputDialogMixin` for row construction.
- **Controllers take the dialog as their first argument**, so behaviour can be exercised
  without building the dialog's whole collaborator graph.
- **A dialog keeps a one-line method for any name another package calls.** Task and
  display bridges hold the dialog reference, so moving a body into a controller must
  not move its externally used name off the dialog.
- **Wiring made against a runtime-built object stays where that object is built** -- a
  per-row browse button, a `QProcess`, the task coordinator, a `QgsMapToolEmitPoint`.
  Only wiring fixed by the form belongs in `qt/bindings/`.
- The map canvas, its map tool and the vertex marker stay on
  `DomainDelineatorDialog`: a tool belongs to the widget that owns the canvas.
- **Dependencies point downward.** `qgis_bridge/` must not reach up into `qt/`. It used
  to call `dialog.load_layer(...)`; that mechanic now lives in `qgis_bridge/layers`,
  which both `display/` and the dialog call into.
- `externals/` imports its external package **inside the functions**. `import
  mhm_tools` costs ~850 ms and pulls in ~1480 modules (geopandas, pandas, rasterio,
  scipy, xarray); at module level that lands on every plugin load. Importing `mhm_qgis`
  must never import `mhm_tools`. `core/handlers/lookup` and `core/handlers/file/netcdf`
  import their third-party libraries lazily for the same reason.

## QGIS Runtime Contract

- Both GUI entry points use real QGIS, never a shim. Install QGIS and its Qt
  binding through the platform's QGIS packages; never add a pip `qgis` or fixed
  `PyQt5`/`PyQt6` dependency to this plugin.
- Use `qgis.PyQt`, scoped enums and `exec()`. `configure_qtpy_api()` selects the
  loaded QGIS Qt major before QtPy/nml-tools/pyqtgraph import. Do not select Qt
  by QGIS major alone or mix bindings in one process.
- The direct launcher initializes Processing through `qgis_bridge/processing`.
  Domain delineation has its own canvas; displays requiring Desktop's `iface`
  do not gain a main map canvas merely by installing runtime libraries.
- Core computations and the native worker remain Qt/QGIS-independent.
- Test QGIS 3/Qt5 and QGIS 4/Qt6 in separate processes before releasing.
  Form compilation on both bindings is not full QGIS workflow verification.
- The current main form contains its pages directly in `tabWidget_steps`, not
  a separate stacked widget. Do not restore duplicate tab/page synchronization.
- L1/L11 text fields accept only compatible grid resolutions and return the
  exact underlying value. Their unit controls display the grid CRS unit, not
  an independent CRS conversion. Invalid text must not become a grid value.

## mHM-Tools Integration

- `mhm-tools>=0.3.0` is an external runtime dependency, pinned in both
  `pyproject.toml` and `requirements.txt`. There is no bundled `src/mhm_qgis/mhm_tools`
  source tree; import the installed package. **The pin currently exceeds what is
  installed in the development environment (`0.2.2.dev4`)** -- keep that in mind before
  reading an import failure as a code bug.
- Call `mhm_tools` through Python functions, not the `mhm-tools` CLI, unless explicitly
  asked, and only through `externals/mhm_tools_handler.py`.
- `rasterize_map_data` burns vector attributes directly or maps them through a lookup
  table, always on the filled DEM's exact grid.
- `format_soil_data`, `format_geology_data`, `format_lai_data` and `format_lc_data`
  accept categorical and DEM rasters as ASCII, GeoTIFF or NetCDF and align categories to
  the DEM with nearest-neighbour resampling.
- Shared raster I/O and alignment live in `mhm_tools.common.file_handler`; lookup-table
  reading in `mhm_tools.common.lookup_handler` (it moved there from `format_data` --
  patch the plugin's own boundary in tests, not mhm-tools' internals);
  `mhm_tools.common.rasterize` is deliberately limited to `rasterize_vector`.
- The matching CLI commands are `data-converter rasterize-map` and
  `data-converter format-data`.
- QGIS-specific layer selection, materialization, logging and output placement stay in
  `mhm_qgis`.
- Do not copy external `mhm_tools` source back into this repository. Put generally
  reusable raster computation in `mhm-tools` and expose it as a public Python function
  plus a CLI command where required.

## Current Conventions

- The selected mHM version comes from `comboBox_mHMversion`.
- File-profile filenames come from the versioned schema configuration. v5.13 uses
  `mhm_parameter.nml`; the current v6 profiles use `parameters.nml` and `outputs.nml`.
- v5.13 schemas live under `nml-schemas/v5.13`, v6 under `nml-schemas/v6`. There is no
  `nml-templates/` directory; nml-tools owns rendering.
- v5.13 multi-domain template values should render as one-shot arrays where possible,
  e.g. `resolution_Hydrology = 12000, 24000`, not repeated `resolution_Hydrology(1)` /
  `(2)` lines.
- UI-facing logs go through `self.log_message(...)`.
- No success popups for routine generated classdefinition files; log silently unless an
  error needs user action.

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
- Configuration lives in `tabWidget_steps` and `stackedWidget_configuration`.
  Only `page_singleDomain` (first work-type option) is implemented; other modes are
  disabled. Do not restore the removed steps stacked widget.
- `pushButton_scanPrecipitationFiles` is the explicit metadata scan boundary.
  The selected folder and relative `lineEdit_precipitationGlobDefinition` determine
  the file set, never a recursive search of every NetCDF in the project. No scan
  runs during project restoration. The worker returns metadata; the controller
  updates coverage, the multiplier default, and the persisted common grid.
- Precipitation Scan and Add/Edit Meteorology open `meteo_var_details_input.ui`.
  Only OK starts metadata inspection; Cancel leaves saved inputs unchanged. The
  variable table is populated after successful inspection and restores without scanning.
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
- Publication watershed-masks **only the DEM**, into a single-variable
  `dem_derivatives_masked.nc`. Slope, aspect, facc, fdir and idgauges go into
  `input.nc` (v6) or the ASCII set (v5) straight from `dem_derivatives.nc`;
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

## Fingerprinted Reuse

- `core/handlers/state/cache.py` records, in `mhm_qgis_processing_state.json`, a
  fingerprint of each stage's inputs next to the outputs it produced. A stage reruns only
  when an input's size or modification time changed, its configuration changed, or one of
  its recorded outputs is gone. Content is never hashed, so the check stays cheap on
  multi-gigabyte inputs.
- Sections in use: `stages` (per Execute All stage, from
  `qt/objects/morphology_tasks.py`) and `meteo_inspection` (per meteorology folder, from
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
- `process_meteo_inputs` takes `progress=`, ticked once per input file and wired
  through `MeteorologyWorkflowWorker.progress` to
  `TaskCoordinator.set_progress`. The workflow runs on a plain `QThread`
  registered with `start_external`, which has no `QgsTask` to report through, so
  without that signal the monitor sits at 0% for the entire run.

## Categorical Morphology Inputs

- Plain input combo boxes are exposed through compatibility adapters under the former
  `mMapLayerComboBox_*` names. Loaded QGIS layers are always listed; folder searching
  (disabled in the form) would add relative project paths. The precipitation folder combo
  never scans the project: it holds a blank entry plus the browsed or saved folder.
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
  adjacent endpoints; v5 transitions must be January 1. Soil horizons are contiguous
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

## Displaying Results

- Morphology output discovery lives in `core/handlers/store/morphology.py`, reusing
  `registry.variants` and `registry.first_existing`. Selection widgets, date ranges and
  warnings belong to `qt/controllers/main.py`; `qgis_bridge/display/morphology.py`
  only renders the resolved output.
- Three group boxes share one render path: `groupBox_displayMorphLayers`,
  `groupBox_meteoDisplay` and `groupBox_outputDisplay`. Each picks a variable and a time,
  then `qgis_bridge/display` puts a 2-D raster on the canvas.
- `core/handlers/file/netcdf` reads the file; `core/post` resolves which variable and
  time step; `qgis_bridge/display` renders. Keep that order -- the resolver is Qt-free
  and testable, the renderer is not.
- `show_raster` reuses the layer already loaded under the same name, refreshing its
  provider when its source or file stamp changes. A time scrubber
  calls it on every step and `layers.load` does not uniquify names, so reloading would
  both stack duplicates in the layer tree and reopen the file each tick.
- Prepared forcing can be addressed through a `NETCDF:"path":var` URI with
  `band = time index + 1`. **mHM output cannot**: it carries no CRS and GDAL returns the
  identity geotransform `(0,1,0,0,0,1)`, which would place it at the origin with unit
  pixels. Output therefore goes through `show_dataarray`, which attaches a CRS and writes
  a scratch raster. The two paths cost the same per tick (measured 220.3 ms vs 224.7 ms),
  so correctness decides, not speed.

## Namelists

- **The plugin no longer renders namelists itself.** It hands schemas, values and
  dimensions to nml-tools, which owns the editor and the rendering. There is no
  `Configuration/` package.
- Schemas ship under `nml-schemas/v5.13` and `nml-schemas/v6`.
- **Every schema file must be reachable from a file profile.** `nml-config.toml`
  registers namelists and the `[[file_profiles]]` lists decide which ones exist;
  a schema that no profile names is dead, and a namelist registered but unlisted
  is invisible in the GUI. v5.13 carried 13 stray v6 schemas (`config_project`,
  `config_time`, `output_mhm`, ... - v6 namelist names mHM 5.13 does not have)
  plus an unlisted `rivertemp1`; all were removed.
- Each version defines its calibration parameter **once** in
  `nml-schemas/parameter.yml` and every parameter property `$ref`s it. The two
  base types are deliberately different and must not be swapped: v6 is the
  `parameter_t` derived type with named `value`/`min`/`max`/`optimize` fields,
  v5.13 is the five-element array `[lower, upper, value, flag, scaling]` that
  mHM 5.13 parses. `parameter.yml` is a `$defs` library, so it is the one
  schema file with no `[[namelists]]` entry. A property that `$ref`s it must not
  also spell out `x-fortran-shape` or `x-nml-tools-ui`, or the two can drift.
  v5.13's `GeoParam` is the one exception: a `[nGeoUnits, 5]` table.
- Version-specific initial values are built by `vSpecific/` (`build_initial_values`,
  `build_dimensions`), which reads `nml-settings.json` through
  `state/nml_settings.load_settings`.
- **v5.13 array sizes are dimensions, not fixed maxima.** `nDomains`, `nGeoUnits`,
  `nSoilHorizons`, `nLcoverScene` and `nGaugesTotal` are named after the namelist
  variables they size, and `build_dimensions` computes each from the same helper
  that builds the values, so a dimension cannot disagree with its array. The two
  gauge/land-cover sizes used to be `[constants]`, which made one land-cover
  scene write 50 entries and one gauge write 200 per domain -- 233 rendered
  lines for a single-domain project where 79 carry information. Do not pad
  values out to a dimension: nml-tools rejects an extent larger than the
  dimension, and `buf` is the only remaining constant.
- nml-tools canonicalises dimension names to **lower case** (`name.lower()` in
  `_normalize_dimensions`), so camelCase in `nml-config.toml` is for readers and
  `default_dimensions` keys come back lowercased. An unknown name raises rather
  than being ignored.
- The handoff goes through `externals/nml_tools_handler.launch_profile_editor`.
  `configure_qtpy_api()` must run before it, because nml-tools loads Qt on first call.
- Use the installed nml-tools API (development checkout now `/home/bashyal/Codes/nml-tools`),
  not a hard-coded source path. `launch_gui(schemas_dir, output_dir, file_profiles, ...)`
  receives the main/parameter/output file-profile dictionary as its third argument.
- nml-tools reads/writes namelist files directly; do not restore its removed nml.json
  document APIs. Shrinking legacy domain arrays is previewed in a scratch workspace
  using schema defaults plus explicit plugin values. Only files actually saved by
  the editor replace originals; keep `.nml.before-single-domain` backups.
- v6 geological-unit parameters map to `parameter_baseflow_1.yml`'s structured
  `baseflow_recession` array; v5 keeps `geoparameter.GeoParam`.
- `qt/controllers/main.py` owns version changes, Edit Namelists and Run mHM; the
  main dialog exposes thin methods wired by `qt/bindings/main.py`. There is no
  configuration processor object.
- The editor runs modally on the QGIS event loop; its return code does not indicate Save.
  Scratch-file modification stamps distinguish saved files from Cancel.
- nml-tools' modal dialogs became tabs (`ProfileDialog` -> `ProfileTab`); check the
  installed API before assuming a class name.
- Preserve full v5.13 parameter templates so blocks like `&PET0` remain present.
- Be careful with Fortran namelist keys containing indices or derived-type fields, e.g.
  `eval_Per%yStart`, `GeoParam(:,1)`, `GeoParam(1,:)`.
- `mhm_qgis_domain_delineation_state.json` remains canonical. Project domain directories,
  `dem.asc` paths, inverse domain-to-gauge membership and gauge metadata into
  `nml-settings.json` before opening nml-tools.
- If changing schemas, keep templates and compatibility mappings in sync.

## Background Processing

- Widgets, `QgsProject`, `QgsMapLayer` and map-canvas operations are main-thread only.
  Snapshot paths, CRS text, numbers and plain containers before starting work.
- Use `TaskCoordinator`/`QgsTask` for path-only computation and return paths or
  serializable results to a main-thread completion callback. `QgsTask` jobs receive only
  copied primitive values and filesystem paths.
- **All heavy categorical formatting runs in `mhm_qgis.core.native_worker`**,
  a child process dispatched through `core/executions/morphology/commands.native_worker`:
  advanced land cover and soil, and the lookup mode used by geology. The lookup path
  used to run inside QGIS and OOM-killed it --
  `anon-rss:5756792kB`, i.e. 5.5 GiB, formatting a vector input onto a 13201 x 6001 grid.
  The worker sets `oom_score_adj=500` so the kernel takes the child first. Cancellation
  must terminate that child; a nonzero or signal exit is reported as a task failure
  rather than crashing QGIS.
- **pyproj must never run on a `QgsTask` thread.** QGIS keeps its PROJ context in
  thread-local storage and tears it down when a task thread ends, leaving libproj's
  cached database context dangling for the next pooled thread: the *second* pyproj
  call in a run segfaults the whole QGIS process in
  `projCppContext::getDatabaseContext`. `osgeo.osr` is unaffected, and so is pyproj on
  the GUI thread or on a plain Python thread. `TaskCoordinator._off_qthread` therefore
  runs every submitted job on a short-lived `threading.Thread`; do not remove that
  hop or call pyproj from a `QgsTask` body directly. This crashed Execute All between
  the hydrology and gauge-position stages.

- **Worker-side modules must not import Qt or QGIS.** Morphology commands live under
  `core/executions/morphology`; QGIS layer extraction/materialization stays under
  `qgis_bridge/layers`, and `core/handlers/lookup` keeps third-party imports lazy. There
  is a subprocess test for this boundary.
- Historical v5.13 land-cover formatting is windowed and period-by-period in `mhm-tools`;
  never restore an eager list of full aligned period arrays.
- Keep large categorical raster algorithms bounded by raster windows and write one
  historical period before allocating the next.

## Import And Reload Pitfalls

- QGIS reload can import `mhm_qgis.py` as top-level `mhm_qgis`; keep reload-safe import
  handling intact.
- Avoid introducing package-relative imports into files that may be loaded top-level
  unless existing code already handles that context.
- Do not reintroduce the deleted `qgis_bridge/morphology` mixin hierarchy. Use explicit
  functions with path/value arguments and keep orchestration in `core/executions`.
- **Relative-import depth is the commonest error when moving a module.** Count from the
  module's own package: level 1 is the containing package, and each extra dot goes up
  one. The current split is `core/morphology` for computation,
  `qgis_bridge/layers` for live QGIS objects and `qt/objects` for scheduling.
- A function-local import is invisible to the test suite until that code path runs. Four
  such imports survived a package move undetected for exactly that reason.
- Naming a module after a stdlib module (`core/utils/time.py`, `core/handlers/file/json/`)
  is safe: Python 3 absolute imports mean `import time` still gets the builtin, which
  wins over any `sys.path` entry.

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

## Packaging Rules

- `.publish/plan.md` is the canonical plan for the PyPI package, QGIS repository ZIP,
  private micromamba QGIS runtime, and standalone/Desktop map-canvas routing. Read it
  before changing publication, runtime bootstrap, entry points, or map-host code.
- `src/mhm_qgis/_version.py` is the single release version source. PyPI reads it
  dynamically; `tools/build_qgis_plugin.py` writes it into the staged QGIS metadata.
- Build PyPI artifacts from the repository root. Build the QGIS upload with
  `python tools/build_qgis_plugin.py`; its ZIP contains only the `mhm_qgis/` plugin
  root plus the repository `LICENSE` and `README.md`.
- Never bundle mHM executables. v5 comes from `mhm==5.13.4`; the machine-local v6
  path lives in `QSettings` (`mhm_qgis/executables/v6`) or `MHM_QGIS_MHMV6`.
- Keep package discovery recursive. Every package needs an `__init__.py` or setuptools
  drops it from the wheel. This bites every time a new subpackage is added -- a
  directory that exists but has no `__init__.py` is not importable and not shipped.
- Keep the `mhm-tools` dependency floor synchronized between `pyproject.toml` and
  `requirements.txt` when a handler starts using a newer public API.
- Include plugin assets, schemas, templates and project-template files in the PyPI build.
- If adding data files under `src/mhm_qgis/`, update `MANIFEST.in` and package-data
  patterns if the extension is new.
- Do not add `qgis` as a PyPI dependency. QGIS is provided by QGIS itself.

## Verifying A Change

- `python3 -m pytest tests/ -q` runs core checks in plain Python and skips GUI
  checks without QGIS. Run it in QGIS Python for the complete suite. Run collection -- know
  it before you start, so a regression is unmistakable.
- `python3 -m compileall -q src/mhm_qgis` catches misplaced imports, broken indentation
  and `__future__` placement. `ast.parse` does **not** enforce `__future__` placement;
  `compile()` does.
- After moving any module, statically resolve every intra-package import against the real
  module tree rather than trusting the test suite -- function-local imports and
  module-level imports in modules no test touches will otherwise pass silently.
- When a file format is a contract (a manifest, a namelist, an ASCII header, geology
  metadata), generate it before and after the change from identical inputs and `cmp` the
  bytes.
- For real-QGIS checks, prepend `/usr/share/qgis/python` and
  `/usr/share/qgis/python/plugins` to `sys.path`, call `QgsApplication.setPrefixPath` and
  `initQgis()`. Set `locale/userLocale` in `QSettings` **after** `initQgis()` or
  `classFactory` fails on a headless machine. Useful assertions: the main dialog
  constructs with 6 input adapters and 6 tabs.
- A shell Python may not have `PyYAML`; QGIS Python may. If schema loading
  fails only because `yaml` is missing in shell Python, note it rather than treating it as
  a plugin failure.
- Do not claim full runtime verification unless it was actually tested inside QGIS.

## Development Workflow

- Use `rg` / `rg --files` for search.
- The worktree may already contain user changes. Do not revert unrelated modifications.
- Many modules import QGIS/PyQt and cannot be fully imported in plain system Python.
- Keep changes narrow and aligned with existing module boundaries.

## Style

- Keep UI code focused on UI state, validation and logging.
- Keep the `externals/` boundary QGIS-free.
- Prefer clear errors over silent fallbacks when the user is expected to provide
  fields/layers.
- Prefer deleting a duplicate over adding a delegator. Several restructuring passes left
  thin forwarding stubs behind; they were removed as redundancy, and new ones are not
  wanted. A one-line method on a dialog is the documented exception, because other
  packages hold a reference to the dialog by name.

## Recent Moves

Recent restructuring passes relocated a lot of code. If a path below looks familiar and
is not found, this is why.

| was | is now |
|---|---|
| `pymhm/` (package and name) | `src/mhm_qgis/` |
| `mhm_qgis_main.py`, `advanced_input_dialogs.py`, `domain_delineator_dialog.py` | `qt/dialogs/*.py`, one per form |
| `mhm_qgis_dialog_base.ui` | `qt/ui/forms/mhm_qgis_main.ui` |
| `ui/`, generated `pyui/` | runtime-loaded `qt/ui/forms/`; generated modules removed |
| `standalone/` shim and launcher | real-QGIS launcher in `qt/__main__.py`; shim removed |
| `mhm_tools_adapter.py` | `externals/mhm_tools_handler.py` |
| `project_layout.py` | `core/handlers/store/{paths,layout,registry}.py` |
| `state_cache.py`, `nml_settings.py` | `core/handlers/state/{cache,nml_settings}.py` |
| `core/handlers/netcdf/` | `core/handlers/file/netcdf/` |
| JSON read/write scattered per module | `core/handlers/file/json/` |
| `time_utils.py` | `core/utils/time.py` |
| `viewport_raster_range.py` | `qt/objects/viewport_raster_range.py` |
| `qgis_compat.py` | `qgis_bridge/layers/compat.py` |
| `load_layer`, layer construction | `qgis_bridge/layers/` (`load`, `open_layer`, `add`, `find_by_*`) |
| `advanced_input_manifests.py` | `core/handlers/lookup/manifests.py` |
| `advanced_input_processing.py` | `core/morphology/layers/advanced_inputs.py`; unused synchronous/ready-input code removed |
| `configuration_processor.py` | dialog-first functions in `qt/controllers/main.py` |
| `morphology_display.py` | `core/handlers/store/morphology.py`; widget logic in `qt/controllers/main.py` |
| `geology_metadata.py` | `core/handlers/lookup/geology.py` |
| `categorical_lookup.py` | `core/handlers/lookup/job.py` |
| `read_categorical_lookup_table`, `read_lookup_fields` | `core/handlers/lookup/table.py` (`read`, `columns`) |
| `qgis_bridge/morphology/` mixin hierarchy | `core/morphology/`, `core/executions/morphology/`, `qgis_bridge/layers/`, `qt/objects/morphology_tasks.py` |
| `core/session/` and `store/state_link.py` | explicit arguments plus `core/handlers/state/processing.py` |
| `others/elevation_bands/` mixins | `core/morphology/elevation_bands/` plus `qt/controllers/elevation_band.py` |
| `core/meteorology/forcing.py`, `inspection_cache.py` | `core/meteorology/forcing/{api,cache,types}.py` |
| `grid_resolution.py`, `core/meteorology/l2_grid.py`, `ERA5Land/mhm/grid.py` | `core/grid/{model,arrays}.py` plus `qgis_bridge/layers/grid.py` |

One known boundary exception remains: `core/handlers/lookup/job.py` calls
`prepare_categorical_file` from `externals/` at call time. Keep the import lazy so
importing the core package does not load mHM-tools.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
