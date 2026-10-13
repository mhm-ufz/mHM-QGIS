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
  QGIS loads this directory directly, and it is what the wheel ships. It holds no
  standalone code: the plugin ZIP is this folder as it is.
- `src/mhm_qgis_runtime/`: the standalone app, only in the PyPI wheel (never in the
  plugin ZIP; it imports `mhm_qgis`, never the reverse). `mhm-qgis` and
  `mhm-qgis-runtime` console entry points. With an importable QGIS it runs `app.main`
  (`python -m mhm_qgis_runtime.app`: a real `QgsApplication` without Desktop) there;
  otherwise it installs a private micromamba QGIS runtime (`envs/mhm-qgis-<version>`)
  and relaunches inside it. The runtime gets `mhm-qgis[full]` with this install's
  mhm-qgis and pure-Python dependencies repacked as wheels (`_local_wheels`), so a
  TestPyPI, local-wheel, editable or unreleased-dependency install needs no index for
  them. `pip install mhm-qgis` installs the launcher only (no dependencies); the app's
  packages are the `full` extra, and the plugin reads `requirements.txt` instead.
- `src/mhm_qgis/metadata.txt`, `__init__.py`, `mhm_qgis.py`: QGIS plugin entry points.
- `src/mhm_qgis/nml-schemas/`: versioned by mHM version, currently `v5.13` and `v6`.
- `pyproject.toml`, `MANIFEST.in`: PyPI packaging metadata.
- `tests/`: core checks run in plain Python; GUI checks require real QGIS and
  are skipped when unavailable. `conftest.py` initializes an offscreen QGIS application.
  Checks that reach GDAL (`osgeo`, which comes with QGIS or the system, not PyPI) call
  `pytest.importorskip("osgeo")`, so CI's pip-only environment skips them instead of
  failing; a new test that reaches GDAL needs the same line.

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
  - `datamodel/`: what a run needs and what it writes. `rules.yml` maps each process case
    to the external inputs mHM reads (v6 `config_processes` ids; v5.13 carries its own
    `processes` with `processCase` indices under `overrides`). `outputs.yml` lists the
    written variables (flux sums versus state means, area weighting, switches,
    per-version file names) and what each post-processing analysis needs. Every rule
    is checked against the mHM source that `sources` names; re-check it there, not
    against memory, before changing one. `check(project)` reports missing inputs with
    their reasons; `check_outputs` and `available_analyses` do the same for outputs.
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
- `intro/`: the introduction wizard's content, Qt-free. `questions.yml` holds the pages
  (info, choice, multiple) and their effects from a closed key set validated on load:
  `processes.*`, `defaults.work_type`, `defaults.l1_factor`/`l11_factor`, `analyses.*`,
  `available.*`. `derive` picks a process case from the data ticked as available, through
  `datamodel.feasible_cases` with a per-version `prefer` order (PET ids differ by
  version). `handoff()` turns the choices into editor values: v6 `config_processes` and
  `out_*` keys; v5.13 the full `processCase` and `outputFlxState` arrays merged into the
  saved namelist. `qt/dialogs/intro_wizard.py` builds a `QWizard` from the schema;
  `qt/controllers/intro.py` applies the answers.
- `externals/`: one handler per external package or executable. Processing calls into
  `mhm_tools`, `nml_tools` and `pyflwdir` go through `mhm_tools_handler.py`,
  `nml_tools_handler.py` and `pyflwdir_handler.py`; `mhm_handler.py` resolves the
  installed `mhm` command (`mhm>=5.13.4,<5.14`, the latest 5.13) for v5 and validates
  the user-selected v6 executable. It tries PATH, the interpreter's scripts folder and
  the user base (`pip --user` in QGIS), skipping a candidate that fails `--version`:
  an old build in `/usr/local/bin` missing its libraries must not hide pip's.
  Heavy imports stay inside functions.
- `vSpecific/`: version-specific initial values handed to the namelist editor.
  The shipped v6 schema's output-switch defaults follow its process defaults by
  mHM v6's rules (`outputs.yml` `needs`/`needs_any`); a test keeps them in step. Array
  defaults are flat lists in Fortran order, with `x-fortran-default-repeat: true` to
  repeat per domain (nml-tools rejects nested ones).
  Morphology publication registers what the published files hold
  (`register_morphology`): a bundle field only when `input.nc` has that variable, and
  for multi-horizon soil the published `soil_horizon_class.nc` with its class variable
  as `soil_class_var` (mHM v6 reads the horizon file through it) and no
  `soil_class_path`. A finished soil or land-cover stage, fresh or reused, records its
  products in its section (`advanced_inputs.record_products`): accepting the input
  dialog again replaces the section with its inputs only.
  v6 hands over input paths only from what the setup prepared (`nml-settings.json`
  sections and `namelist_values`), never hard-coded defaults; only output and restart
  paths are the plugin's own. The editor keeps values already saved in `mhm.nml`
  (initial values can add or change a field, not unset it).

Top-level modules that remain include `mhm_qgis.py` (plugin lifecycle, reload-safe
imports) and `dependency_bootstrap.py`. The main form's controllers are split by
concern -- `main` (input sources, outlets, domain definition, result display),
`grid` (L0/L1/L11/L2, precipitation reference, common grid), `forcing` (meteorology
and observation variables), `categorical` (land cover, soil, geology, LAI input
types), `input_state` (save/restore), plus `project`, `model`, `time`, `version` and
`workflow_state`. Workflow scheduling lives in `qt/workflows/`.
`qt/controllers/elevation_band.py` is kept deliberately for a future Elevation Bands
feature. The form has no `pushButton_elevation_bands` / `pushButton_bandDetails` now,
so it is unwired; connect `process_elevation_bands` and `process_band_details` in
`qt/bindings/main.py` when those buttons return. Do not delete it as dead code.

## Qt Layering

A module belongs to exactly one layer.

- `qt/ui/forms/`: Qt Designer source, one `.ui` per dialog.
- `qt/ui/__init__.py`: one `load_form` helper using `qgis.PyQt.uic.loadUiType`.
  Forms are the runtime source of truth: no `pyui/`, `pyuic5` or `pyrcc5` step.
  Icons use paths relative to the form; promoted-widget headers use `qgis.gui`.
  Keep XML enum spellings compatible with Qt5 Designer (e.g. `Qt::Vertical`);
  the runtime loader generates the binding-specific Python. Qt6 Designer saves
  scoped spellings (`Qt::Orientation::Vertical`, `Qt::AlignmentFlag::AlignLeft`,
  `QDialogButtonBox::StandardButton::Ok`) that PyQt5's uic cannot compile; after a
  Qt6 Designer save run
  `sed -i -E 's/\b(Qt|Q[A-Za-z]+)::[A-Za-z]+::([A-Za-z0-9_]+)/\1::\2/g' <form>.ui`.
  `test_designer_forms_and_icons[PyQt5]` fails on any that remain. `resources.qrc` is
  retained only for Designer asset editing, never imported at runtime.
- `qt/bindings/`: connects a signal to a slot and nothing else.
- `qt/controllers/`: what a widget shows and whether it is enabled -- reads and writes
  widget values, populates combos, validates and warns.
- `qt/workflows/`: the direct UI entry points for morphology, meteorology and
  observation execution. They build requests and schedule work; core owns computation.
  - `lifecycle.start(dialog, key, build, submit)` is the one path every Execute button
    takes: refuse while a workflow runs, check prerequisites, `build()` the request
    (`ValueError`/`OSError` become the user's message), `workflow_state.begin`, submit,
    and `workflow_state.finish` on the first reported outcome. At most one Execute
    workflow runs; `workflow_state.running_key` reads it, and `finish` ignores a second
    outcome from the same pipeline.
  - `morphology.py`: Execute All's stages, plus `start_ready_input` for a newly chosen
    mHM-ready input. `hydrology.py`: DEM derivatives and flow/channel files on demand,
    for Execute All's first stage and for the Domain Delineator, domain assignment and
    elevation bands.
  - A stage reports failure only through its `failed` callback (`lifecycle.fail`); the
    caller decides whether to show it. A prerequisite is scheduled first, explicitly
    (`start_domain_preflight`, `start_ready_input`), never by a stage re-calling itself
    after running what it lacks.
  - Workflows read form state through controllers and the dialog's public methods
    (`grid.common_l0_header`, `forcing.observation_specs`, `categorical.advanced_input`),
    not through the dialog's private attributes.
- `qt/dialogs/`: the dialog classes themselves, one module per form.
  `MhmQgisDialog` owns its logging and prerequisite checks directly.
- `qt/threads/`: `task_coordinator.py`, the one background mechanism (real QgsTask
  scheduling). The native raster subprocess entry point belongs to `core/`, not this
  Qt package.
- `qt/tooltip/`: the Help tour. `Closure.helpRequested` starts it on the visible page:
  the current `tabWidget_steps` tab plus any visible stacked page inside it, with steps
  from `tooltips.yml` keyed by object name, in list order. A step whose widget is hidden
  or missing is skipped. The tour is a child overlay with its own outline frame; an
  event filter on the dialog keeps it placed and makes Esc end the tour rather than close
  the dialog. **Renaming or removing a widget named in `tooltips.yml` means updating the
  schema**; `test_tooltip.py` fails otherwise. New form widgets get a step there too.
- `qt/objects/`: a long-lived `QObject` attached to a widget rather than defined by one
  -- `viewport_raster_range.py` watches a map canvas and keeps a renderer in step. Not
  keyed to a single form, so it belongs in none of the three layers above.

Rules that follow from it:

- **One `bindings` module per form, named after the form; a `controllers` module only
  where it earns its keep.** The large main form splits controllers by concern (see
  Package Layout). Dialogs whose behaviour is small, or tied to a widget they build
  (Domain Delineator's canvas, discharge-assignment rows, the task monitor, the
  terminal), keep it as their own methods: a controller that only the dialog calls,
  through a forwarder that calls straight back, is two hops for one. The
  land-use/soil dialogs share `_DynamicInputDialogMixin` for row construction.
- Fixed form buttons connect directly to their controller or workflow in `bindings/`.
  Put a one-line `button → target` comment at that transition. Do not restore string
  dispatch through the dialog. Runtime-built row buttons stay beside row construction.
- **Controllers take the dialog as their first argument**, so behaviour can be exercised
  without building the dialog's whole collaborator graph.
- **A dialog keeps a one-line method only for a name another package calls** --
  `qt/workflows`, `qgis_bridge/display`, `vSpecific`, another dialog, or a test that
  stubs it. Controllers and `bindings/` call each other's functions directly, never
  back through the dialog: `MhmQgisDialog` used to carry 73 forwarders, most of them
  loops from its own controller back into that controller. Real signatures only, no
  `*args, **kwargs`.
- **Access form widgets directly.** Forms are loaded at runtime, so a missing widget is
  a bug; `getattr(dialog, "pushButton_x", None)` guards hid 17 widgets that had left
  the form, and the code wired to them.
- A bound QObject slot, not a lambda, goes on a `QgsProject` or application signal
  (`refresh_input_sources`, the teardown guards), so the connection dies with the
  dialog on plugin reload.
- **Wiring made against a runtime-built object stays where that object is built** -- a
  per-row browse button, a `QProcess`, the task coordinator, a `QgsMapToolEmitPoint`.
  Only wiring fixed by the form belongs in `qt/bindings/`.
- `qt/dialogs/map_canvas.py` is a programmatic window (no form), so its wiring and
  drop handling live in that module rather than in `bindings/` and `controllers/`.
  File drops are handled by the window itself: the canvas and layer tree ignore file
  drags, so Qt passes them up to it.
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
- `QgsApplication` takes bytes argv: `QgsApplication(sys.argv, ...)` raises
  `TypeError: expected bytes`. Pass `[]`, as `mhm_qgis_runtime/app.py` and `conftest.py` do.
  `test_launcher_opens_and_closes_the_real_application` is the only test in which `main()`
  owns its application.
- Use `qgis.PyQt`, scoped enums and `exec()`. `configure_qtpy_api()` selects the
  loaded QGIS Qt major before QtPy/nml-tools/pyqtgraph import. Do not select Qt
  by QGIS major alone or mix bindings in one process.
- The direct launcher initializes Processing through `qgis_bridge/processing`.
- One map host, chosen by `MhmQgisDialog.iface`. Desktop passes its real `iface`, and
  Map Canvas raises Desktop's canvas; the Desktop dialog is shown non-modally (no
  `exec()`) so that canvas stays usable. Standalone (`iface=None`) lazily creates
  `qt/dialogs/map_canvas.py`, parented to the dialog. The parent keeps it alive, keeps it
  interactive under the dialog's modal `exec()`, and deletes it before `exitQgis()`.
  Its canvas follows `QgsProject` through `QgsLayerTreeMapCanvasBridge` (tree order,
  visibility checkboxes, zoom to the first layer); never add a second sync path or
  a fake `iface`. Domain Delineator keeps its own canvas.
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

## Displaying Results

- Morphology output discovery lives in `core/handlers/store/morphology.py`, reusing
  `registry.variants` and `registry.first_existing`. Selection widgets, date ranges and
  warnings belong to `qt/controllers/main.py`; `qgis_bridge/display/morphology.py`
  only renders the resolved output.
- Every display ends in `qgis_bridge/display/canvas.py` (`show_raster`,
  `show_dataarray`). After a successful load, it calls the dialog's one-line
  `map_displayed(when)` hook, which opens the standalone map window and sets its time
  label. Meteo passes `when=`; morphology and grid pass nothing, which clears it.
- Two group boxes share one render path: `groupBox_displayMorphLayers` and
  `groupBox_meteoDisplay`. Each picks a variable and a time, then `qgis_bridge/display`
  puts a 2-D raster on the canvas. mHM output has its own tab (below).
- `show_raster` reuses the layer already loaded under the same name, refreshing its
  provider when its source or file stamp changes. A time scrubber
  calls it on every step and `layers.load` does not uniquify names, so reloading would
  both stack duplicates in the layer tree and reopen the file each tick.
- Prepared forcing can be addressed through a `NETCDF:"path":var` URI with
  `band = time index + 1`. **mHM output cannot**: it carries no CRS and GDAL returns the
  identity geotransform `(0,1,0,0,0,1)`, which would place it at the origin with unit
  pixels. The Outputs tab therefore wraps it in a VRT (`post_canvas`, below).
- **Outputs tab** (`tab_outputs` in `mhm_qgis_main.ui`, so Designer shows it): context bar
  (run, basin, period, resolution), the navigation list, "Analysis layout" / "Reset
  layout" and the status row; nothing else. `qt/controllers/post_context.py` owns
  `dialog._post` and its `AnalysisContext` (`qt/objects/analysis_context.py`, one
  `changed(field)` signal; unchanged values emit nothing). Views: `post_balance`
  (Overview, Water balance, Processes), `post_map` (Map, Map controls, Cell inspector),
  `post_hydro` (Hydrograph, Signatures, Events, Climatology), `post_validation`.
  - **Where the views go** (`qt/docks/host.py`, `layouts.yml`, post plan §0c): the
    Overview stays in the tab (`textBrowser_postOverview`, refreshed whether or not the
    analysis layout is on). Every other view has a **central panel**, a page added to the
    `QStackedWidget` that holds the host's main canvas (QGIS 4.2.3: page 0 is
    `theMapCanvas`; found as `iface.mapCanvas().parentWidget()`), and **left docks**
    tabbed with QGIS's "Layers" panel (`add_dock`; tabs at the bottom; Map Explorer's Cell inspector on the right). The central widget
    itself is never replaced. **QGIS shows the canvas again whenever a layer is added**
    (verified): clicking the navigation entry again (`itemClicked`) brings the panel
    back. The standalone `MapCanvasWindow` is built alike (canvas in a stacked widget,
    the layer tree as a left dock named "Layers"), so there is one code path.
  - **Map Explorer draws on the host's main canvas.** That canvas follows the project's
    layer tree in both hosts, so a copy of the map and fresh overlays go into the
    temporary group "mHM post (temporary)" (`post_map._sync_main`, only while Map Explorer
    is shown: adding a layer would hide another view's panel), removed by
    `post_map.leave` when the layout ends. Our click tool replaces the user's map tool
    while the view is shown and gives it back after; clicks are transformed from the
    canvas CRS (the project's) to the run's. "Keep in project" copies the map into the
    permanent "mHM post" group. The other views draw on the Map dock, out of the project.
  - Panels and docks are made on first use from their forms, and their widgets are
    reached as `dialog._post.ui.<name>`, never `dialog.<name>`. Selecting a view applies
    its saved state (QSettings `mhm_qgis/post/layout/<view>`, `STATE_VERSION` 2) or its
    `layouts.yml` layout; the workspace keeps the user's own layout
    (`mhm_qgis/post/user_layout`) and restores it when turned off (panels and docks are
    deleted). In QGIS Desktop the visible layout comes back exactly; a few bytes of the
    saved state differ, in hidden QGIS docks' remembered sizes. One shared Map dock: the
    views with a map of their own show it there (`post_map.set_view_layers`); Compare's
    three maps are its central panel. Only visible panels and docks refresh; hidden ones
    collect the changed fields and refresh when shown.
  - **No netCDF read on the GUI thread** (a test patches `netCDF4.Dataset` to fail there):
    opening a run, building VRTs, gauge series, scores and derive passes go through
    `qt/workflows/post.submit` (coordinator, resource `post-io`; a request that arrives
    while the same job runs waits and then runs, latest wins). The open task reads
    `run.variables` so the pages can use them; `scan_job(run=...)` reuses that run.
  - Tier B pages: Signatures, Events and Climatology (`post_hydro`), Validation
    (`post_validation`). Signatures and events need daily or finer discharge and two
    years; rainfall joins an event only at the discharge's own step (monthly P is never
    spread onto days); the runoff ratio uses totals, so any P step works.
  - Tier C views (`post_advanced`, navigation group "C · Advanced"): Compare (run B is
    opened and scanned on its task, without a series cache when that would be large;
    Map B and Map Δ docks beside the shared Map, extents linked by `link_extents`),
    Extremes (annual maxima and minima, Gringorten and the GEV from ten years; maps: Q
    maximum, mRM bankfull by mhm-tools as a `derive("bankfull")` worker job), Drought
    (day-of-year Q80 events at the gauge; SMI from the `SM_Lall` series cache, the SMI of
    the current month and the longest drought; no frequency map, it is uniform by
    construction), Anomalies (`checks.run` findings, a row jumps to its cell, gauge or
    month; the z map of the current month), Summary (`balance.summary`, basins coloured
    by a column, smallest on top). "Why here?" is the map's right-click menu: a dock
    tabbed with the Cell inspector (`post_context.open_dock`), outside every view's
    layout, so it hides on a view switch. A layer shown on a canvas other than the shared
    Map must be kept referenced (`state.widgets`): canvases do not own their layers.
  - **Export** (`post_export`, the context bar's Export menu; no report): the visible
    docks' tables (CSV, as shown minus thousands separators), plots (pyqtgraph PNG, SVG
    and each plot's curves as CSV) and maps (every raster on a visible canvas, the shown
    band of an instant map, as GeoTIFF and CF netCDF by `gdal.Translate` on a task: the
    VRT reads the netCDF output). The menu also archives the current run.
  - **Observation operators** (`core/post/observe.py`, a `derive("observe")` worker
    job): windows are the coarser record's intervals, used only when the finer one covers
    them completely; fluxes are summed and states time-weighted. mhm-tools'
    `resample_to_reference_windows` averages everything (wrong for summed fluxes; a test
    pins it) and is not used. Grids: exact block mean onto the coarser one; unknown
    observation units are refused. TWS anomaly and snow cover go to mhm-tools (lat/lon
    runs only; projected runs are refused with that reason). **Stamp files for mhm-tools
    at interval centres:** it selects a window's steps with an end-inclusive slice, so an
    edge stamp lands in two windows. The native worker sets `MPLBACKEND=Agg` (headless;
    in a VS Code snap terminal a GUI backend crashed the process).
  - **pyqtgraph and teardown:** a dialog holding pyqtgraph items must be deleted by Qt
    (`deleteLater`), not left to Python's cycle collector, or the session teardown
    segfaults. Pages make their plots only once a run is open; tests that open one end
    with `deleteLater` + `sendPostedEvents(DeferredDelete)`.
- **Post-processing engine** (`core/post`, Qt-free; plan `.development/.post`):
  - `runs.py`: the current `output/` plus archived runs in `runs/<name>/`. Archiving
    moves `output/` with `os.replace` (no copy), moves output files the namelists put
    elsewhere and copies the restart, each to the same relative path, so a run's
    files resolve alike current or archived. A v5.13 multi run lists one run per
    domain (`output/<domain>/`); v6 domains are listed and archived one by one. The
    caller refuses archiving while mHM runs.
  - **Named runs** (the Execute tab's `lineEdit_runName`): before mHM starts,
    `runs.link_outputs` rewrites the output keys of `mhm.nml` (`dir_Out`, `dirConfigOut`,
    `output_path`, `output_node_path`, `output_poi_path`, `diagnostics_path`; never
    `dir_Total_Runoff`, an input, nor restart paths) to `runs/<name>/…` and creates the
    folders; an unnamed run points them back to `output/`. After a successful run,
    `finish_named` copies the namelists and writes `run.json` with `linked: true`: its
    paths resolve from the setup folder (`RunRef.root` = setup), an archived run's from
    its own folder. `run.json` is read beside the run's `namelists/`. The current run
    (`output/`) resolves its paths without a named run's `runs/<name>/` prefix, which the
    setup's namelists keep after a named run (`runs.unlinked`).
  - **A run with every output off** writes only the grid (no time axis): `read_bounds`
    raises a `ValueError` saying so, the run's findings show it in the Overview, and Run
    mHM asks before such a run (`datamodel.outputs_on`).
  - **mHM v6 drops a switched-on output while no active process provides its field**
    (`mhm_filter_output` in `exchange/mo_mhm_container.F90`; v6 defaults every process
    case to 0). `outputs.yml` mirrors that contract: `needs` (all) and `needs_any` (one
    of, e.g. aET, Q, satSTW), v6's rule "case /= 0" as case lists that v5.13's cases also
    pass. Run mHM lists the outputs mHM would drop (`datamodel.dropped_outputs`), and the
    Outputs tab says why one is missing (`datamodel.why_not_written`).
  - `catalog.py`: file locations come from the namelists that ran, through
    `outputs.yml` `files` (never globbed); variables are the file's joined with
    `outputs.yml` `outputs`; time comes from `time_bnds` only (units whitespace
    normalised). `findings()` checks bounds, the L1 cell size and the domain area
    against `ConfigFile.log` (0.5 %).
  - `grid.py`: basin weights are L0 masks binned into L1 cells (exact block mean, as
    mHM computes its L1 cell areas). Domain coverage comes from the restart's
    `L1_domain_cellarea`, else the L0 DEM, else whole valid cells; `sealed_fraction`
    from the restart's `L1_fSealed`, else L0 land cover == 2 × `fracSealed_cityArea`.
    The restart is used only when the namelists say it was written.
  - `scan.py` (in the native worker) is the only full read of an output file: blocks
    of whole time steps within `budget_mb`, so every (1, ny, nx) chunk is decompressed
    once. It builds the opt-in cell series cache, basin series (F8: pervious and sealed
    states weighted by 1 - fSealed or fSealed per scene; storage amounts per basin area,
    intensities such as `SM_L` per weighted area), per-cell statistics, monthly totals
    (NaN for months the run covers in part), annual extremes and a colour range.
    Precipitation comes from the L2 forcing, each L1 cell taking its L2 cell, summed per
    output interval; intervals the forcing does not cover stay NaN.
  - `cache.py`: `.temp/Post/<run>_<domain index>/`. A variable is rescanned only when the
    fingerprint of its sources changes, so `executions.post.scan_job` rewrites a grid
    product only when its content changes (its mtime is a source). The series cache is
    checked against free disk space before the scan; above 2 GB the UI asks first.
  - `series.py`, `aggregate.py`, `balance.py` read only the cache (`.npy`, `index.json`
    with each variable's kind, allowed statistics and data-model key), so the GUI thread
    may call them; `series.gauge` reads netCDF (coordinator task). Series are indexed
    by interval start; intervals come from `bounds_<file>.npy`, never from the stamp.
  - Semantics are enforced: a state's sum is refused ("is a state: sum is meaningless"),
    flux means are mm per day from the intervals, state means are time-weighted, and a
    day, month or year the run covers only in part is NaN. **Calendar lengths come from
    `scan.calendar_days`:** casting `timedelta64[M]` to days gives an average month
    (30.44 d) and would blank every February.
  - The balance runs over intervals 2..T (ΔS = S_T - S_1, fluxes from interval 2):
    exact for model-step output; otherwise `bound` is the full neighbouring-interval
    change at each end (a bound while storage is monotone across that pair; half of it
    underestimated a fast recession on the fixture). R is "unaccounted, incl. karstic loss".
  - `metrics.kge` is mhm-tools' (`externals.mhm_tools_handler.kling_gupta`), so scores
    match its evaluations; importing mhm-tools takes about 0.9 s, so metrics run on a
    coordinator task. Below `min_pairs` every score is NaN with a `reason`.
- **Post map layer** (`qgis_bridge/display/post_canvas.py`, renders only): each
  (run, variable) is a VRT in the run's cache, one band per time step pointing into the
  netCDF (no copy), placed from the axis bounds, CRS from the setup or EPSG:4326 for
  geographic runs. **GDAL's netCDF driver serves some files south-first (F16: Rapti, not
  the fixture)**, so `_served_flipped` compares GDAL's first band with netCDF and the VRT
  gets a south-up geotransform then; QGIS reads both north-up (tested on Rapti). Time is
  `renderer.setInputBand` (no file I/O); bands carry fixed temporal ranges from
  `time_bnds`. Colours are fixed from the scan's 2nd/98th percentiles (Viridis) or
  centred on 0 (RdBu) for differences. Keep a GDAL dataset referenced while using its
  band; edit graduated classes through the source symbol, never `ranges()` copies; a
  raster `clone()` drops the fixed classification, so "Open in QGIS" copies the style
  with `QgsMapLayerStyle`.

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
  also spell out `x-fortran-shape` or `axes`, or the two can drift.
  v5.13's `GeoParam` is the one exception: a `[nGeoUnits, 5]` table. Axis labels and
  table layout sit at a property's top level (`axes`, `table`); nml-tools still reads the
  older `x-nml-tools-ui` wrapper. An unvisited page renders an array's repeated default
  (`x-fortran-default-repeat`, e.g. v5.13 `GeoParam`) through nml-tools' `suggestion`
  (fixed in nml-tools 2026-10-10). **Still open in nml-tools:** an unvisited required array of
  derived items with item defaults but no top-level `default` (v6 `baseflow_recession`)
  raises "has no value"; setups with prepared geology hand it over and don't hit it.
- **nml-tools project profiles** (`[[project_profiles]]` in `nml-config.toml`) group the
  file profiles of one workflow: `v5` (v5.13: `main`, `parameter`, `output`, `mrm_output`),
  `classic` (v6 single domain: `main` = `mhm.nml`, `parameter`, `output`) and `combine` (v6
  multi-domain: `meta` = `combine.nml`, `parameter`, `output`). `paths.namelist_project(version,
  combined)` picks one; the editor and every read name it, so v6's `main` and `meta` are
  never mixed (`load_project(..., None)` loads every file profile).
- `externals/nml_tools_handler.read_namelists(folder, schemas_dir, project=...)` reads the
  project's saved files through our schemas (nml-tools `gui.model`), so values are typed and
  group and key names are lower case; `core.datamodel.read_namelists(setup)` picks the
  schemas and the project from the setup's version and kind.
- v5.13 `processCase(1..11)` is interception, snow, soil moisture, direct runoff, PET,
  interflow, percolation, **routing, baseflow, neutrons**, river temperature
  (`common/mo_common_variables.F90`). The editor's axis labels follow that order; they
  had routing, baseflow and neutrons rotated. PET ids differ by version: v5.13 `0` is input
  with aspect correction, v6 `-2` is, and v6 `0` is off. v6 routing case 1 is not
  implemented in the v6 app.
- `nml-settings.json` `namelist_values` holds **v6 field names only** (`config_input`,
  `config_observations`). `state/nml_outputs.register_meteo` and `register_observations` take
  a required `v6` flag from their callers (the state package must not import the store
  layout); for a v5.13 setup they drop the section instead of writing it, since v5.13 reads
  its inputs from the fixed `dir_*` folders that `vSpecific/v5_13` builds.
  `apply_registered` overlays `namelist_values` for v6 only.
- The wizard's process and output choices are **one-shot**. They go into the
  `nml-settings.json` section `intro_values`, which `vSpecific.common.apply_registered`
  overlays after the plugin-managed `namelist_values` (both versions). `model.edit_namelists`
  empties it once a `.nml` file in the setup has changed, i.e. after the editor saved,
  so later edits in the editor win. `namelist_values` itself is re-applied on every launch:
  keep it to values the plugin owns (paths, variable names).
- Run mHM first calls `model.inputs_ready`: `datamodel.check` lists every input the saved
  process cases read but nobody prepared, logs each with what needs it, and asks to run
  anyway. mHM stays the final judge.
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
- The handoff goes through `externals/nml_tools_handler.launch_editor(schemas_dir,
  output_dir, project, namelists=..., initial_values=..., initial_dimensions=...)`.
  `configure_qtpy_api()` must run before it, because nml-tools loads Qt on first call.
  `model.edit_namelists` picks the project from the setup; the optimisation and parameter
  buttons narrow it (`{file profile: [namelist]}`, nested under the project), with `main`
  mapped to `meta` in a combined setup. nml-tools preloads an explicitly selected project
  into its tree (`launch_gui` passes `preload_projects`), so the editor opens on it.
- Use the installed nml-tools API (development checkout now `/home/bashyal/Codes/nml-tools`),
  not a hard-coded source path. `launch_gui(schemas_dir, output_dir, project_profiles, ...)`
  takes a project name or `{project: {file profile: [namelist]}}`; nml-tools **rejects
  unknown profiles, namelists and fields**, so `build_initial_values` names only the
  project's profiles (a test checks it). Dimensions are always passed: v6 `n_domains`,
  `n_geo_units` and, with soil set up, `max_layers` = the horizon count (nml-tools'
  defaults are 3, 16 and 6); `config_project.max_layers` and `soil_depth` follow the same
  horizons, unpadded.
- nml-tools reads/writes namelist files directly; do not restore its removed nml.json
  document APIs. Shrinking legacy domain arrays is previewed in a scratch workspace
  using schema defaults plus explicit plugin values. Only files actually saved by
  the editor replace originals; keep `.nml.before-single-domain` backups.
- v6 geological-unit parameters map to `parameter_baseflow_1.yml`'s structured
  `baseflow_recession` array; v5 keeps `geoparameter.GeoParam`.
- `qt/controllers/model.py` owns version changes, Edit Namelists and Run mHM, wired
  directly by `qt/bindings/main.py`. There is no configuration processor object.
- The editor runs modally on the QGIS event loop; its return code does not indicate Save.
  Scratch-file modification stamps distinguish saved files from Cancel.
- nml-tools' modal dialogs became tabs (`ProfileDialog` -> `ProfileTab`), and a tab builds
  its pages lazily (`tab.values()` covers unvisited ones); check the installed API before
  assuming a class name.
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
  copied primitive values and filesystem paths. Every runner is called as
  `runner(task)`; it reports with `task.setProgress`, stops on `task.isCanceled()`, and
  logs with `coordinator.log.emit(key, message)`, which reaches both the dialog log and
  the task monitor from any thread. Never call a widget, including `log_message`, from
  a runner.
- `log_message` does not pump the event loop. It used to call `processEvents()` per
  line, so a click could run half-way through another handler.
- **All heavy categorical formatting runs in `mhm_qgis.core.native_worker`**,
  a child process dispatched through `core/executions/morphology/commands.native_worker`:
  advanced land cover and soil, and the lookup mode used by geology. The lookup path
  used to run inside QGIS and OOM-killed it --
  `anon-rss:5756792kB`, i.e. 5.5 GiB, formatting a vector input onto a 13201 x 6001 grid.
  The worker sets `oom_score_adj=500` so the kernel takes the child first. Cancellation
  must terminate that child; a nonzero or signal exit is reported as a task failure
  rather than crashing QGIS.
- **Native-worker progress:** the parent adds `"progress": <temp>/progress.txt` to every
  job; a job that reports writes an integer 0-100 there atomically (`native_worker._progress`)
  and the parent's 0.2 s poll passes it to `task.setProgress`. Post-processing uses job kinds
  `post-scan` and `post-derive` (`core/executions/post.py`).
- **pyproj must never run on a `QgsTask` thread.** QGIS keeps its PROJ context in
  thread-local storage and tears it down when a task thread ends, leaving libproj's
  cached database context dangling for the next pooled thread: the *second* pyproj
  call in a run segfaults the whole QGIS process in
  `projCppContext::getDatabaseContext`. `osgeo.osr` is unaffected, and so is pyproj on
  the GUI thread or on a plain Python thread. `TaskCoordinator._off_qthread` therefore
  runs every submitted job on a short-lived `threading.Thread`; do not remove that
  hop or call pyproj from a `QgsTask` body directly. This crashed Execute All between
  the hydrology and gauge-position stages.
- **A `QgsTask.fromFunction` result is tested for truth** (`if self.returned_values:` in
  QGIS's wrapper): a falsy result reaches the callback as nothing, and a DataFrame raises
  there, silently, leaving the task unfinished and the coordinator busy for good. The
  coordinator boxes every result (`[result]`) and unboxes it in `finished`; keep that.

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
  root (with its `LICENSE`, `COPYING`, `COPYING.LESSER`, `AUTHORS.md`) plus the
  repository `README.md`. The license is LGPLv3+, like mhm-tools: those four files exist
  at the repository root and as identical copies in `src/mhm_qgis/` (a test keeps them
  equal), so the plugin folder zipped by hand carries them too.
- Never bundle mHM executables. v5 comes from the `mhm` package (latest 5.13); the machine-local v6
  path lives in `QSettings` (`mhm_qgis/executables/v6`) or `MHM_QGIS_MHMV6`.
- Keep package discovery recursive. Every package needs an `__init__.py` or setuptools
  drops it from the wheel. This bites every time a new subpackage is added -- a
  directory that exists but has no `__init__.py` is not importable and not shipped.
- Keep the `mhm-tools` dependency floor synchronized between `pyproject.toml` and
  `requirements.txt` when a handler starts using a newer public API.
- Include plugin assets, schemas, templates and project-template files in the PyPI build.
  Every `*.yml` under `src/mhm_qgis/` ships (package-data `**/*.yml`, `MANIFEST.in`
  `recursive-include`; the plugin ZIP allows `.yml`), so a new YAML schema needs no
  packaging change.
- If adding data files under `src/mhm_qgis/`, update `MANIFEST.in` and package-data
  patterns if the extension is new.
- Do not add `qgis` as a PyPI dependency. QGIS is provided by QGIS itself.
- The build backend is pinned to `setuptools>=69.3,<70`. 69.3 is the first version with
  the PEP 625 sdist name PyPI requires (`mhm_qgis-X.tar.gz`). 77+ rejects the current
  `license = {file}` table plus license classifier, so moving past 70 means switching to
  an SPDX `license` string first.
- `.gitlab-ci.yml` runs on every push to `upstream` (`git.ufz.de/mhm/mhm-qgis`), shaped
  like mhm-tools' pipeline: `isort --check`, `ruff check src tests tools`, the plain test
  suite on Python 3.11 and 3.13 with coverage, and `build` (wheel, sdist, `twine check`,
  plugin ZIP). `test-qgis` runs the whole suite inside the private QGIS 3.44 runtime
  through `tools/run_in_runtime.py`; it downloads a conda QGIS, so it runs on a
  schedule, on tags or by hand. The local QGIS suite stays the gate for a change.
- `.github/workflows/release-check.yml` runs on the GitHub `origin` (tags, manual,
  weekly). It builds the artifacts, runs core tests on Python 3.11 and 3.14, and installs
  the private runtime plus a GUI open/close on Linux, Windows and both macOS
  architectures. Publishing stays manual.
- Until mhm-tools 0.3 and nml-tools 0.6 are on PyPI, both pipelines install them from
  pinned commits, one `pip` call each with `SETUPTOOLS_SCM_PRETEND_VERSION` set to the
  version pymhm pins: git checkouts carry no matching tag, and hatch-vcs ignores the
  per-package `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_*` variables (measured: they installed
  as `0.2.4.dev57` and `0.1.dev285`). Move a pin when the local checkout the
  suites ran against moves; delete the block at the PyPI release.
- The private runtime is `src/mhm_qgis_runtime/locks/<platform>.lock`, generated from
  `tools/runtime/environment.yml` with `uvx conda-lock lock -f tools/runtime/environment.yml
  --virtual-package-spec tools/runtime/virtual-packages.yml --kind explicit
  --filename-template 'src/mhm_qgis_runtime/locks/{platform}.lock' -p <platform>`.
  Lock one platform per run: a failing platform makes conda-lock write nothing, and a
  solve needs ~4 GB RAM and ~10 min. Every compiled dependency of mhm-qgis, mhm-tools
  and nml-tools belongs in `environment.yml`; pip may add only the pure/self-contained
  `mhm`, `mhm-tools`, `nml-tools`, `easy_mpl` and `cdo` wheels. Otherwise PyPI wheels
  bring their own GDAL/PROJ into the QGIS process. conda-forge `mhm` and `python-cdo`
  are deliberately absent: the first pins an older libnetcdf than QGIS, the second
  pulls the CDO binary, which has no Windows or current macOS build.
- Runtime subprocesses get `PYTHONNOUSERSITE=1` and no `PYTHONPATH`/`PYTHONHOME`.
  Without that, stale `~/.local/lib/python3.X` packages shadow the locked ones, and pip
  uninstalls user-site packages while upgrading them.

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
  `/usr/share/qgis/python/plugins` to `sys.path` **after `src`**:
  `PYTHONPATH=src:/usr/share/qgis/python:/usr/share/qgis/python/plugins`. The plugins
  folder holds the `mhm_qgis` symlink to the main checkout, so with it first a worktree
  or scratch copy silently tests the main checkout's code. Then call `QgsApplication.setPrefixPath` and
  `initQgis()`. Set `locale/userLocale` in `QSettings` **after** `initQgis()` or
  `classFactory` fails on a headless machine. Useful assertions: the main dialog
  constructs with 2 input adapters (DEM, pour points) and 6 tabs.
- `PyYAML` is a declared dependency (`pyproject.toml`, both `requirements.txt`), so
  `dependency_bootstrap` checks it before the dialog opens; the plugin's own schemas
  may use `yaml.safe_load`.
- Do not claim full runtime verification unless it was actually tested inside QGIS.
- A test that opens a pyqtgraph window must `deleteLater()` its dialog and flush
  `DeferredDelete` before returning, while it still holds the plot. Left to the cycle
  collector, pyqtgraph's C++ items can outlive their Python wrappers and segfault the
  session teardown in `conftest.pytest_sessionfinish` (exit 139 after every test passed;
  `test_discharge_graph.py` did this in about one run in three).

## Development Workflow

- Use `rg` / `rg --files` for search.
- The worktree may already contain user changes. Do not revert unrelated modifications.
- Many modules import QGIS/PyQt and cannot be fully imported in plain system Python.
- Keep changes narrow and aligned with existing module boundaries.

## Style

- Lint: `isort .` then `ruff check src tests tools` (config in `pyproject.toml`). isort
  is the only import sorter, so ruff's `I` rule is off. `D`, `EM`, `PTH` and `ARG` are a
  deferred ratchet: don't add new violations of them. `RUF046` stays off because NumPy 1.x
  `round()` of a float32 returns float32, so `int(round(x))` is deliberate. A `noqa`
  carries its reason.

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
| `standalone/` shim and launcher | real-QGIS launcher in `mhm_qgis_runtime/app.py`; shim removed |
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
| `qgis_bridge/morphology/` mixin hierarchy | `core/morphology/`, `core/executions/morphology/`, `qgis_bridge/layers/`, `qt/workflows/morphology.py` |
| `core/session/` and `store/state_link.py` | explicit arguments plus `core/handlers/state/processing.py` |
| `others/elevation_bands/` mixins | `core/morphology/elevation_bands/` plus `qt/controllers/elevation_band.py` |
| `core/meteorology/forcing.py`, `inspection_cache.py` | `core/meteorology/forcing/{api,cache,types}.py` |
| `grid_resolution.py`, `core/meteorology/l2_grid.py`, `ERA5Land/mhm/grid.py` | `core/grid/{model,arrays}.py` plus `qgis_bridge/layers/grid.py` |
| `MhmQgisDialog` forwarders, input-state save/restore, grid methods | `qt/controllers/{input_state,grid,categorical,forcing}.py`; the dialog keeps only names other packages call |
| grid, meteorology inputs and categorical handlers in `qt/controllers/main.py` | `qt/controllers/grid.py`, `forcing.py`, `categorical.py` |
| `qt/threads/meteo_workflow.py`, `TaskCoordinator.start_external` | `qt/workflows/meteorology.py` submitting an ordinary coordinator task |
| `qt/controllers/{domain_delineator,discharge_assignment,thread_display,project_terminal}.py` | methods on their dialogs |
| `MorphologyTaskBridge.run_*`, per-step morphology buttons | removed with the buttons; Execute All's stage map in `start_execute_all` |
| `LookupConfigDialog` (`qt/dialogs/single_layer_lookup.py`) | removed; unused outside tests |
| `MorphologyTaskBridge.start_hydrology`, `start_domain_preflight`, `prepare_filled_dem` | `qt/workflows/hydrology.py` functions taking the dialog |
| `execute_all_active`, `finish_morphology_workflow`, `set_meteo_setup_controls_enabled` | `workflow_state.begin` / `finish` / `running_key`, driven by `qt/workflows/lifecycle.py` |

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
