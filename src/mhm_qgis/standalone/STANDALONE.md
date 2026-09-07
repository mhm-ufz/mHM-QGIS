# Strategy for running without QGIS

This is a proposed direction, not an implementation report. The target is a
standalone desktop application that prepares the same mHM projects without a QGIS
installation. Qt can remain; QGIS-free does not mean GUI-free or GDAL-free.

## Main idea

Make the application a file-based mHM preparation engine with a Qt interface.
Keep QGIS as an optional integration for selecting layers and displaying results,
not as a requirement for computation or project management.

Keep one Python distribution, one set of processing functions and one project
format. The standalone launcher and QGIS plugin should call the same execution
APIs. Do not build a second implementation under `standalone/`.

## What already exists, and what is missing

- `standalone/cli.py` already opens the main dialog using `qgis_shim.py`.
  This is a startup bridge, not full standalone processing. `--no-gui` currently
  exits with an informational message; it is not a batch-processing command.
- Much of the engine is already reusable: `core/grid`, `core/meteorology`, raster
  jobs, advanced categorical preparation, delineation Show/Save commands, state
  persistence and output discovery.
- `qt/threads/task_coordinator.py` already has a QThread fallback. Heavy raster
  subprocesses already use the Qt/QGIS-free `core/native_worker.py`.
- Remaining dependencies include snapping, domain/gauge preparation, some CRS and
  extent extraction, QGIS layer selectors, and map rendering. Execute All has a
  shared stage list, but its snapping stage still executes QGIS geometry code
  through `qt/objects/morphology_tasks.py`.
- Import independence alone is insufficient. For example,
  `core/morphology/hydrology/outlets.py` still accepts dialog/layer-shaped objects,
  and `vSpecific` still reads values from a dialog. These interfaces need ordinary
  records and settings, even where there is no direct QGIS import.

Do not expand the shim into a replacement GIS. Its coordinate transforms return
unchanged coordinates, geometry distance returns zero, and Processing raises an
unavailable-backend error. Those stand-ins cannot establish spatial correctness.
Until a workflow has a real backend, disable its standalone action with a clear
explanation rather than allowing placeholder geometry to produce results.

## Responsibility boundaries

Paths below are relative to `src/mhm_qgis/`.

| Responsibility | Home and strategy |
| --- | --- |
| Workflow ordering and commands | `core/executions/`: complete the existing APIs so every stage can run from explicit inputs, independently of a dialog. |
| DEM, outlets, snapping, domain/gauge rules | `core/morphology/`: retain the current algorithms; extract necessary computation from QGIS layer handling into path/record-based functions. |
| Resolutions, CRS calculations and common extents | `core/grid/`: retain the exact-grid contract and accept raster metadata, bounds and CRS strings, not live layers. |
| Raster/vector/table access | Reuse existing file, raster and lookup handlers. Add `core/handlers/file/vector/` only if shared vector I/O has no suitable home; keep domain rules outside it. |
| Project paths, files and saved state | Existing `core/handlers/store/` and `core/handlers/state/`; no parallel standalone state store or new session manager. |
| External processing packages | Existing `applications/` handlers for mHM-tools, pyflwdir and nml-tools; retain lazy imports. |
| Widgets, selection, progress and previews | `qt/`, divided into the existing form/controller/binding/thread roles. No standalone computation here. |
| Live QGIS objects and canvas | `qgis_bridge/`, loaded only by the plugin path. Export provider-backed layers to files before calling shared APIs. |
| Startup | `standalone/`: launch the Qt application and select its presentation behavior. Retire the shim once no supported standalone path needs it. |

Use existing dictionaries and small records for paths, outlet IDs, coordinates,
CRS, resolutions and options. Pass progress, logging and cancellation explicitly.
Do not replace `dialog` with another large object holding the entire application.

## Migration order

### 1. Complete one file-only preparation workflow

Start with local DEM, pour-point, categorical, LAI and meteorology files. Keep the
current execution plans and make their inputs constructible without widgets.
Do not duplicate Execute All ordering in the standalone launcher.

Prioritize the remaining spatial boundary:

- Read point/channel records from files, including an explicit layer or variable
  name where a container has multiple datasets.
- Extract snapping and gauge/domain membership from
  `qgis_bridge/layers/domain.py` and `gauges.py` into the appropriate core APIs.
  Reuse the installed spatial stack through existing handlers; no homemade CRS
  conversion or geometry engine.
- Preserve snapping behavior: metres from project settings, count every feature
  inside the buffer, prefer stream order then distance, use nearest actual
  geometry when no candidate is inside, and retain confidence values.
- Obtain merged-domain bounds and raster metadata from files. Use real CRS
  transformations and bounds that cover the transformed domain, not the shim's
  unchanged rectangles or an unsafe two-corner approximation.
- Reuse the existing delineation Show/Save computations. Move remaining assignment
  validation and state coordination out of QGIS-specific workflow objects as
  needed; do not rewrite watershed algorithms that already work without QGIS.

A small test project should reach model-ready outputs through the shared APIs
before investing in a new map viewer. A public batch CLI can follow later; it is
not required to deliver a standalone desktop application.

### 2. Make the shared Qt interface genuinely QGIS-independent

Keep the current PyQt interface and form/controller/binding structure. Import the
actual Qt binding without installing fake `qgis` modules. Keep any necessary
binding compatibility in one small Qt import boundary.

Use ordinary file selectors and a CRS entry/selector validated by the real CRS
library. Remove QGIS custom-widget requirements from shared Designer forms,
including `QgsProjectionSelectionWidget`, then regenerate `pyui/` using the
existing script. Never patch generated UI code by hand.

The QGIS plugin can optionally supply loaded-layer choices and canvas display.
Connect those capabilities at startup using a few explicit callbacks where
needed; do not scatter runtime checks through processing code or introduce a
general backend framework. The shared GUI must also work with only local files.

### 3. Reuse the existing task execution machinery

Keep QThread scheduling for the standalone GUI and the native subprocess worker
for memory-heavy raster jobs. Keep any QgsTask implementation in the optional
QGIS integration. Both should invoke identical core commands.

Snapshot inputs on the GUI thread; return results and errors to that thread for
display. Preserve bounded concurrency, output-resource exclusion, progress,
cancellation and child-process termination. Headless checks should use the same
commands without creating a Qt application or pretending to be QgsTask.

### 4. Add only the spatial interface the user needs

For the first usable standalone release, allow outlet selection and coordinate
entry in a table and show prepared masks in a lightweight Qt raster preview.
Keep the current explicit Show Delineation action, confidence colours, cached-mask
reuse and Save behavior. Save must still prepare every outlet mask and merge only
active domains, with the optional DEM domain.

Map clicking, pan/zoom and channel overlays can follow on that preview when
needed. Display decimated rasters, but transform picked coordinates using the
original georeferencing. Reuse core output discovery and time/band selection for
morphology, meteorology and mHM results. Do not build a full GIS layer tree,
provider system, browser application or online map service to reach this goal.

QGIS may retain the richer canvas while the standalone GUI has a smaller viewer;
both must use the same saved choices and processing results. Document any remaining
interaction differences rather than claiming complete UI parity.

### 5. Finish configuration and installation boundaries

Keep nml-tools for the editor and `vSpecific` for version-specific values. Pass
saved configuration and exact resolutions into value builders instead of asking
them to read dialog methods. Do not create another namelist renderer.

The mHM executable is a separate runtime requirement. Make its availability and
working directory explicit, report its exit status, and do not depend on QGIS's
shell environment. A later batch command can reuse the same run configuration.

Audit `pyproject.toml`, `requirements.txt` and packaging together. Dependencies
directly used by supported preparation workflows must be declared or supplied by
a documented installation path, rather than inherited accidentally from QGIS.
The current stack includes native GDAL/OGR/PROJ components: removing QGIS does not
remove that installation requirement. Keep Qt/editor dependencies optional only
if a genuinely GUI-free installation becomes a supported target.

First prove installation and execution in a clean environment with no QGIS.
Defer a frozen executable, installers and a package rename until that works.

## Contracts to preserve

- Keep the same project-local `settings.yaml`, state JSON files, reuse fingerprints
  and version-aware paths. A project should reopen in either application without
  conversion; do not rely on a QGIS project file for essential state.
- Keep the user's final L2 multiplier, exact common L0/L2 extents and alignment
  validation. Never resample prepared L0 data or derive grids from rounded UI text.
- Preserve staging before publication, categorical/LAI padding and masking rules,
  and domain DEMs on the common L0 extent. Keep v5.13 and v6 output contracts intact.
- Retain the single processing-state update boundary. If standalone workers gain
  direct state writes across processes, verify cross-process serialization rather
  than assuming an in-process lock is sufficient.

## Definition of done

Use a clean environment without QGIS or the shim to verify:

1. The desktop application starts, selects files/CRS, saves and reloads a project.
2. A small real-data workflow completes snapping, delineation, morphology,
   meteorology, common-grid publication and namelist preparation for both supported
   mHM layouts. Unsupported operations fail explicitly.
3. The same shared API runs from a test without Qt, a dialog or QGIS objects.
4. Plugin and standalone outputs agree in grids, values, nodata, outlet choices and
   state meaning. Compare contractual text bytes; compare NetCDF data and metadata
   semantically rather than assuming container bytes must match.
5. Cancellation, missing dependencies, stale caches and resuming a saved project
   behave correctly. A separate plugin smoke check still passes inside real QGIS.

Existing shim-based tests remain useful during migration, but they are not proof
of a QGIS-free processing application.

The shortest path is to finish the file-based execution boundary first, remove
QGIS requirements from the shared Qt interface second, and add only the standalone
visualization needed to operate the model.

## Domain delineator as a standalone first step

### Is it possible with the current configuration?

Yes, without rewriting the watershed engine or waiting for the entire plugin to
become standalone. However, the current interactive dialog cannot run unchanged.

The distinction is:

| Part | Current position |
| --- | --- |
| Watershed calculation, mask merging and catchment area | Already available through QGIS-free raster jobs and `core/executions/morphology/delineation.py`. |
| Preview-cache persistence and reuse | Already available through `core/handlers/state/domain_state.py`. |
| Form layout and button wiring | Largely reusable. `domain_delineator_dialog.ui` already has an ordinary `QWidget` named `widget_mapCanvasHost`; QGIS is inserted by the dialog, not required by that host. |
| Interactive view | Still requires `QgsMapCanvas`, map tools, markers, raster renderers and QGIS layers. These are not supplied by the standalone shim. |
| Snapping, assignments and complete Save | Still partly coupled to `DomainWorkflow`, QGIS features/table layers and main-dialog methods. These need file/record-based interfaces. |
| Opening the dialog | `MhmQgisDialog.open_domain_delineator()` explicitly blocks standalone mode. Removing this guard alone would not make the dialog work. |

A small synthetic-DEM check was run with imports of Qt, QGIS and the shim blocked.
The existing Show command, preview-state persistence, cache reuse and mask Save
completed successfully. This verifies that subset of the engine, not the
interactive interface, automatic snapping or the complete domain/gauge commit.

### Smallest useful implementation path

1. **Give the delineator explicit inputs instead of the main dialog.** Start with
   a project folder, an already filled DEM, pour-point file and ID field, CRS,
   domain/gauge choices and discharge paths. Reuse the project's settings and
   saved state. Pass only the needed logging, scheduling and completion callbacks;
   a Qt parent should provide widget ownership, not hidden processing inputs.
   Preparation from a raw DEM and channel-network generation can call the existing
   morphology commands when required.

2. **Finish the domain-specific core interfaces.** Extract snapping, outlet
   validation and gauge membership from `qgis_bridge/layers/domain.py` into
   `core/morphology/hydrology/`. Keep coordinate calculations in `core/grid/`
   and file reading/writing in the existing handlers. For discharge data, reuse
   `core/handlers/file/discharge.py`'s file parsers and formatting logic, but let
   its writer accept records or a path instead of requiring a table layer.
   `OutletAssignment` should similarly carry a discharge path, not a QGIS object.
   Test gauge membership against the saved mask raster; no polygon conversion is
   needed just to determine which domains contain a point.

3. **Make complete Save an execution API, not a dialog callback.** Currently,
   `delineation.save()` prepares mask files and returns metadata. It does not
   perform everything in `DomainDelineatorDialog._commit_domain_state()`: gauge
   membership, observation writing, output registration, detailed state saving
   and `nml-settings.json` synchronization still happen elsewhere. Bring that
   orchestration under `core/executions/morphology/`, using the existing state,
   store and discharge APIs. Validate assignments before writing and report
   failures without marking an incomplete save successful. The dialog should
   collect choices, request the operation and display its result.

4. **Replace only the QGIS presentation.** Keep one shared dialog, controller and
   bindings module. Put a small Qt image/graphics view in `widget_mapCanvasHost`
   for standalone use; leave the QGIS canvas implementation in `qgis_bridge/`.
   Start with an outlet list, editable coordinates, flow-accumulation or DEM
   background and translucent mask overlay. A coordinate-entry prototype can
   precede map clicking, but it is not full interaction parity. Add picking and
   pan/zoom to the same view rather than introducing a GIS framework.

   Use the filled DEM CRS as the initial display CRS. Transform input points and
   channels into it using the real spatial libraries. For picks, convert view
   coordinates through the displayed image transform back to original DEM
   coordinates; account for zoom, downsampling and raster orientation. Validate
   against the original DEM extent and nodata. Downsample for display only, never
   for watershed computation, and draw cached masks using their saved `mask_value`.

5. **Launch and schedule without the plugin.** A thin standalone entry can create
   the Qt application, collect those inputs and open the shared delineator
   directly. It need not construct `MhmQgisDialog`, a fake `QgsProject` or a second
   workflow engine. Reuse the existing Qt task coordinator's non-QGIS execution
   path after separating its imports; keep worker inputs free of widgets and
   layers. Preserve cancellation and close/busy guards. Enable the existing
   standalone button only when its selected backend supports the full operation.

### Behavior that must remain identical

- Next, outlet selection and manual location changes do not calculate watersheds.
  They display a valid cached mask if available. Show Delineation explicitly
  calculates a missing mask and persists preview metadata without committing draft
  domain/gauge choices.
- Save prepares masks for every listed outlet, merges only active domains plus
  the optional DEM domain, and commits paths, observations and state. Domain DEM
  files remain a later Morphology Setup product on the common L0 grid; standalone
  delineation must not substitute tightly cropped DEMs.
- Automatic snapping retains the project-local metre-based buffer, candidate
  counting, highest-order/distance preference and nearest-geometry fallback.
  Confidence remains 0/red, 1/yellow or 2/green. Manual locations have no automatic
  confidence and must not be overwritten during reopening.
- Keep outlet IDs, mask fingerprints, draft-versus-committed state and relative
  paths compatible with existing projects. Use the same files in QGIS and
  standalone mode, not separate copies of the project state.

### A practical first milestone

Open a prepared project without QGIS, select an outlet, enter or pick its
coordinates, click Show, navigate away and back without recalculation, then Save
all masks and reopen the project with its choices intact. Include a gauged outlet
so the test exercises observation files and the complete state commit, not just
mask generation. Check changed-point/DEM cache invalidation, different input CRSs,
outside/nodata picks, cancellation and automatic snapping separately.

The recommendation is to make this the first complete standalone feature: reuse
the existing watershed engine, extract the remaining domain workflow from QGIS,
and replace only its view. Do not extend the shim or fork the entire dialog.
