---
name: mhm-qgis-display
description: "Showing layers and results in QGIS Desktop or the standalone map window, and the post-processing views. Read before changing map display, the map window or the Outputs tab."
---

# Displaying results

Part of the mhm-qgis agent instructions; the index is in `AGENTS.md`.

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
