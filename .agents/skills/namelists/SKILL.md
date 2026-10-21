---
name: mhm-qgis-namelists
description: "nml-tools schemas, the namelist editor hand-over, registered values and v5.13/v6 differences. Read before touching namelists, schemas or the namelist editor."
---

# Namelists

Part of the mhm-qgis agent instructions; the index is in `AGENTS.md`.

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
