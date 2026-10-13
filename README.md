# mHM QGIS — QGIS plugin for Mesoscale Hydrological Model

Prepare model inputs and namelists for **mHM 5.13 and v6**, then run mHM, in QGIS
or as a standalone application.

- Delineate domains, snap outlets, and assign discharge observations.
- Prepare DEM derivatives, soil, land cover, geology, and LAI.
- Align morphology and meteorology on compatible model grids.
- Edit namelists with nml-tools, guided by an introduction wizard, and combine prepared
  domains into one multi-domain run.
- Run mHM as named cases, each in its own `runs/<name>/` folder, and analyse them in the
  Outputs tab: water balance, hydrographs and scores, signatures, events, validation
  against observations, run comparison, extremes, droughts and plausibility checks.

## Install

### Standalone application (PyPI)

```bash
python -m pip install mhm-qgis
mhm-qgis
```

This works in any Python 3.11+: plain, a virtual environment or a conda environment.
`pip install mhm-qgis` installs only the small launcher. On first launch, after you
confirm, it installs a private QGIS 3.44 runtime with the app and everything it needs
(conda-forge and PyPI), then opens the app; later launches open it directly. Your own
environment is not changed. The runtime needs about a 1 GB download and several GB of
disk, and no administrator rights. It goes into:

- Linux: `~/.local/share/mhm-qgis` (or `$XDG_DATA_HOME/mhm-qgis`)
- macOS: `~/Library/Application Support/mhm-qgis`
- Windows: `%LOCALAPPDATA%\mhm-qgis`

If the environment already has QGIS (for example `conda install -c conda-forge qgis`),
install the app's packages there instead and `mhm-qgis` uses that QGIS:

```bash
python -m pip install "mhm-qgis[full]"
```

On an HPC, run the first launch on a login node (it downloads), set `XDG_DATA_HOME` if
the home quota is small, and use a desktop session or X11 forwarding for the window.

Manage the runtime without opening the GUI:

```bash
mhm-qgis-runtime install --yes   # unattended installation
mhm-qgis-runtime status
mhm-qgis-runtime remove          # repair = remove, then install
```

### QGIS plugin

Install **mHM QGIS** from the QGIS Plugin Manager (QGIS 3.44 or newer, including
QGIS 4). Enable *Show also experimental plugins* while the plugin is experimental. On
first start the plugin lists its missing Python dependencies (see `requirements.txt`)
and offers to install them with `pip --user` into the Python that QGIS uses. If that
fails, install them manually with that same Python. On Windows, use the OSGeo4W Shell:

```bash
python -m pip install --user -r <plugin folder>/requirements.txt
```

### mHM executables

mHM 5.13 (the latest 5.13 release) is installed with the `mhm` Python package, as the
`mhm` command. For mHM v6, choose a
compatible executable in the Configuration tab (or set `MHM_QGIS_MHMV6`).

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

In **5. Outputs**, pick a run (the current `output/`, a named run or an archived one)
and a view. Each view arranges the QGIS main window (or the standalone map window): its
panel in the centre, its side panels as tabs beside Layers; Map Explorer draws on the
map canvas itself. **Export** writes the shown tables, plots and maps to a folder.

Displayed layers appear on QGIS Desktop's map canvas. In the standalone application
they appear in its own map window, which has a layer tree and accepts dropped raster
and vector files. The Map Canvas button focuses whichever canvas is in use.

## Development

```bash
git clone https://git.ufz.de/mhm/mhm-qgis
cd mhm-qgis
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/ -q
```

For QGIS Desktop development, symlink `src/mhm_qgis` into your QGIS profile's
`python/plugins/` directory. GUI tests require real QGIS and run offscreen; they are
skipped when its runtime libraries are unavailable. Core tests remain
QGIS-independent. Designer `.ui` files load at runtime through `qgis.PyQt`, so no
UI or resource compilation is needed.

See [CONTRIBUTING.md](https://git.ufz.de/mhm/mhm-qgis/-/blob/main/CONTRIBUTING.md) and
[CHANGELOG.md](https://git.ufz.de/mhm/mhm-qgis/-/blob/main/CHANGELOG.md).
