---
name: mhm-qgis-qgis-runtime
description: "How the plugin uses QGIS (Qt5/Qt6, Processing), mhm-tools through its adapter, and background tasks. Read before touching QGIS APIs, mhm-tools calls or long-running jobs."
---

# QGIS, mhm-tools and background work

Part of the mhm-qgis agent instructions; the index is in `AGENTS.md`.

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
  `exec()`) so that canvas stays usable. Standalone (`iface=None`) lazily creates an
  independent top-level `qt/dialogs/map_canvas.py` window. A local event loop waits for
  the non-modal dialog, and the dialog's `finished` signal closes the map before
  `exitQgis()`.
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
