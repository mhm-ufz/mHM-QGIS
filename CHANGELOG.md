# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

First public release, as a PyPI package (`mhm-qgis`) and a QGIS plugin (`mhm_qgis`).

### Added
- mHM 5.13 and v6 setups: domain delineation and outlet snapping, DEM derivatives,
  soil, land cover, geology and LAI, and meteorological forcing on aligned L0/L1/L11/L2
  grids.
- Namelist editing through nml-tools, and running mHM: v5.13 from the `mhm` PyPI
  package (its latest 5.13 release, as the `mhm` command), v6 from a user-selected
  executable.
- A standalone `mhm-qgis` command that reuses an importable QGIS or installs a
  private, checksum-verified QGIS 3.44 runtime on first launch. `mhm-qgis-runtime
  install|status|remove` manages it.
- A standalone map window with a layer tree (visibility, ordering), cursor and time
  read-outs, and file drops. In QGIS Desktop, Map Canvas focuses Desktop's canvas.
- Supports QGIS 3.44+ (Qt5) and QGIS 4 (Qt6).
- Multi-domain runs: combine prepared single-domain setups into one mHM run (work type
  "Combine Existing Domains"). The plugin checks that the domains agree and builds
  `combine.nml` (v6) or one `mhm.nml` with per-domain arrays (v5.13).
- An introduction wizard (Launch on the Configuration tab): a few questions about the study
  and the available data set the work type, resolutions and PET method, and pre-fill the
  process and output choices in the namelist editor.
- Named runs: give a run a name on the Execute tab and mHM writes its outputs to
  `runs/<name>/` in the setup (the namelists point there); the Outputs tab lists the run
  by name, so cases can be told apart and compared.
- Run mHM lists inputs that the selected processes read but are not prepared yet, and
  asks before running.
- A guided tour from the Help button: it steps through the visible page's main inputs
  with a short explanation each.
- Net radiation (`netrad`) as a meteorology input, so PET Priestley-Taylor and
  Penman-Monteith (process cases 2 and 3) can be prepared.
- An Outputs tab for analysing finished runs: pick the current or an archived run, a
  basin, a period and a resolution; Overview (water balance, ratios, gauge scores and
  findings), Map Explorer (every output variable over time or as a period statistic,
  click a cell for its values and series), Hydrograph (observed and simulated discharge
  with precipitation and scores), Water balance (tables, monthly bars, per-cell maps),
  Processes, Signatures (flow duration curve and hydrological signatures), Events (flood
  events ranked, with their errors), Validation (the run against gridded observations of
  ET, snow, soil moisture or neutrons, TWS anomalies and snow cover through mhm-tools, and
  every gauge's scores), Climatology, and the advanced views: Compare (two runs side by
  side with their difference), Extremes (annual extremes and return periods, bankfull
  discharge), Drought (streamflow droughts and the soil moisture index), Anomalies
  (plausibility checks) and Summary (every basin in one table). Right-click a map cell
  for "Why here?". Export writes the shown view's tables, plots and maps (CSV, PNG, SVG,
  GeoTIFF and netCDF) to a folder. Each run is scanned once in the background; runs
  can be archived before the next one overwrites `output/`. Each view opens in QGIS's
  main window (or the standalone map window): its panel in the centre, over the map
  canvas, and its side panels as tabs beside Layers and Browser; Map Explorer draws on
  the main map canvas itself. The layout you adjust is remembered per view, and turning
  "Analysis layout" off restores your own.

### Changed
- Morphology publication masks the flow direction to the domain (v5.13 `fdir.asc`; v6
  `data/morph/fdir.nc`, registered as `fdir_path`, while `input.nc` keeps the full one):
  mHM v6 builds its grid from fdir's cells, so a full fdir ran MPR over the whole DEM
  extent and could exhaust memory.
- v6 namelist defaults: mHM's standard process set (routing case 3), the output
  switches on for every output those processes provide (as mHM v6 writes them), and
  monthly night fractions repeated for each domain.
- The namelist editor opens nml-tools' project for the setup: `classic` for a v6 single
  domain (mhm.nml), `combine` for a v6 multi-domain run (combine.nml) and `v5` for mHM 5.13,
  which now includes the mRM output namelist (`mrm_outputs.nml`); the routed-discharge
  output of v5.13 runs is checked like the others.
- PyYAML is a declared dependency; the plugin reads its own YAML schemas.
- GitLab CI runs isort, ruff, the test suite (Python 3.11 and 3.13) and the build on
  every push; the QGIS suite inside the private runtime runs on a schedule, on tags or by
  hand.

### Fixed
- The LAI time step chosen in the LAI dialog (`lai_time_step`) reaches the namelist
  editor; Execute All writes the LAI handoff again from that choice.
- Multi-horizon soil reaches the v6 namelist editor: the prepared
  `soil_horizon_class.nc` is registered with its variable name, and its paths survive
  accepting the soil dialog again (no `soil_class_path` pointing at a bundle without
  soil classes).
- The v6 namelist editor no longer receives input paths for data the setup never
  prepared (an `idgauges.asc` as SCC gauges, land-cover, LAI and lookup-table files):
  only the prepared inputs are handed over.
- A run with every output switched off (mHM then writes only the grid) is explained in
  the Outputs tab instead of failing with "time not found", and Run mHM asks before
  starting such a run.
- mHM v6 silently dropping switched-on outputs whose process is off (every v6 process
  defaults to off): Run mHM now lists them before the run, and the Outputs tab says a
  missing output's process was off instead of asking to turn its switch on.
- After a named run, the current `output/` run no longer opens the named run's files.
- An old `mhm` build on PATH that cannot start (missing libraries) no longer hides the
  `mhm` command installed by pip.
- A background task whose result was empty or a table no longer stays "running" forever
  (QGIS tests a task's result for truth; results are now passed through unchanged).
- The historical land-use dialog opens when saved periods lie outside the simulation
  period: it shows them clamped to the period, with a note, so they can be corrected.
- v5.13 setups no longer get v6 `config_input` entries in `nml-settings.json` from the
  meteorology and observation runs, and the v5.13 namelist editor ignores any left over.
- The v5.13 process editor labelled processCase 8-10 as Baseflow, Neutrons, Routing;
  mHM reads them as Routing, Baseflow, Neutrons.
