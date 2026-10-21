---
name: mhm-qgis-architecture
description: "Package and module layout, the Qt layering rules (controllers, workflows, dialogs, forms) and import/reload pitfalls. Read before adding, moving or wiring modules and UI code."
---

# Architecture

Part of the mhm-qgis agent instructions; the index is in `AGENTS.md`.

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
