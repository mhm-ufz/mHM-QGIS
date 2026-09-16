# mHM QGIS — QGIS plugin for Mesoscale Hydrological Model

Prepare model inputs and namelists for **mHM 5.13 and v6** in QGIS.

- Delineate domains, snap outlets, and assign discharge observations.
- Prepare DEM derivatives, soil, land cover, geology, and LAI.
- Align morphology and meteorology on compatible model grids.
- Edit namelists with nml-tools and inspect rasters or discharge time series.

This plugin prepares and configures mHM; running simulations requires a
separately installed `mhm` executable.

## Install from source

```bash
git clone https://git.ufz.de/mhm/mhm-qgis
cd mhm-qgis
python3 -m pip install -e .
```

For QGIS, copy or symlink `src/mhm_qgis` into your QGIS profile's
`python/plugins/` directory and enable **mHM QGIS** in the Plugin Manager.
Install Python dependencies into the environment used by QGIS.

## Workflow

In **0. Configuration**, select a project folder, then open a saved domain or
choose **<new>** and press **Create Project Structure**. Each setup has its own
settings, state and outputs under `<project>/mhm-qgis/<domain-name>/`; legacy
workspaces are left untouched.

Choose a DEM, configure precipitation's folder, glob, units and cadence, then
confirm its metadata dialog to scan and establish the common grid. Add other
meteorology variables through the same dialog. Choose simulation dates and
catchments; no selected catchments means valid DEM cells.

Run **Execute All Morphology**, then **Execute All Meteorology**. Review inputs,
edit namelists, and run mHM. Each setup is one model domain and may contain
disconnected catchments. Meteorology preparation preserves input cadence,
converts units and optionally deaccumulates running totals. Historical land-use
periods share exact datetime boundaries (v5 changes must fall on year boundaries).
Both model versions can generate L0/L1/L11/L2 coordinates in `data/latlon.nc`.

To open the GUI without QGIS Desktop, run `mhm-qgis` (or
`python -m mhm_qgis.qt`) from a Python environment with the real QGIS runtime
and Processing libraries installed. There is no QGIS-free GUI or compatibility
shim. Install QGIS and its matching Qt binding through your platform's QGIS
packages, not pip. Map previews that use QGIS Desktop's main canvas still
require Desktop; the domain delineator owns its own canvas.

The UI targets QGIS 3/Qt5 and QGIS 4/Qt6. Designer `.ui` files are loaded at
runtime through `qgis.PyQt`; no UI/resource compilation is needed. Full QGIS 4
workflow verification is still pending.

## Development

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/ -q
```

GUI tests require real QGIS and run offscreen; they are skipped when its
runtime libraries are unavailable. Core tests remain QGIS-independent.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).
