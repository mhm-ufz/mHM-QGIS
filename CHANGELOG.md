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
- Namelist editing through nml-tools, and running mHM: v5.13.4 from the `mhm` PyPI
  package, v6 from a user-selected executable.
- A standalone `mhm-qgis` command that reuses an importable QGIS or installs a
  private, checksum-verified QGIS 3.44 runtime on first launch. `mhm-qgis-runtime
  install|status|remove` manages it.
- A standalone map window with a layer tree (visibility, ordering), cursor and time
  read-outs, and file drops. In QGIS Desktop, Map Canvas focuses Desktop's canvas.
- Supports QGIS 3.44+ (Qt5) and QGIS 4 (Qt6).
- Net radiation (`netrad`) as a meteorology input, so PET Priestley-Taylor and
  Penman-Monteith (process cases 2 and 3) can be prepared.

### Changed
- PyYAML is a declared dependency; the plugin reads its own YAML schemas.
- GitLab CI runs isort, ruff, the test suite (Python 3.11 and 3.13) and the build on
  every push; the QGIS suite inside the private runtime runs on a schedule, on tags or by
  hand.

### Fixed
- The v5.13 process editor labelled processCase 8-10 as Baseflow, Neutrons, Routing;
  mHM reads them as Routing, Baseflow, Neutrons.
