"""Small path-based adapters for the installed :mod:`mhm_tools` package."""
from __future__ import annotations

import contextlib
import io
import logging
import os
from collections.abc import Callable, Generator
from pathlib import Path
from tempfile import TemporaryDirectory

from ..core.handlers import lookup

LogCallback = Callable[[str], None]

# The raster name states the format: mhm-tools takes it as its `-e` extension
# and names the file after it, so the two can never drift apart.
_OUTPUTS = {
    "soil": ("format_soil_data", "soil_class.nc", "soil_classdefinition.txt"),
    "geology": (
        "format_geology_data",
        "geology_class.nc",
        "geology_classdefinition.txt",
    ),
    "lc": ("format_lc_data", "lc.nc", None),
    "lai": ("format_lai_data", "lai_class.nc", "LAI_classdefinition.txt"),
}


def categorical_output_suffix(kind: str) -> str:
    """Return the file extension `prepare_categorical_file` writes for `kind`."""
    return Path(_OUTPUTS[kind][1]).suffix


class _StreamToCallback(io.TextIOBase):
    """Forward completed stream lines to a callback."""

    def __init__(self, callback: LogCallback, prefix: str = "") -> None:
        self._callback = callback
        self._prefix = prefix
        self._buffer = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buffer += str(text)
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._emit(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""

    def _emit(self, line: str) -> None:
        line = line.rstrip("\r")
        if line:
            self._callback(f"{self._prefix}{line}")


class _CallbackLoggingHandler(logging.Handler):
    """Forward logging records to a callback."""

    def __init__(self, callback: LogCallback) -> None:
        super().__init__()
        self._callback = callback
        self.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._callback(self.format(record))
        except Exception:
            self.handleError(record)


@contextlib.contextmanager
def capture_messages(
    callback: LogCallback | None,
    logger_names: tuple[str, ...] = ("mhm_tools",),
) -> Generator[None, None, None]:
    """Capture tool stdout, stderr, and logging into ``callback``."""
    if callback is None:
        yield
        return

    stdout = _StreamToCallback(callback)
    stderr = _StreamToCallback(callback, prefix="ERROR: ")
    handlers: list[tuple[logging.Logger, logging.Handler, int, bool]] = []
    try:
        for logger_name in logger_names:
            logger = logging.getLogger(logger_name)
            handler = _CallbackLoggingHandler(callback)
            handlers.append((logger, handler, logger.level, logger.propagate))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            yield
    finally:
        stdout.flush()
        stderr.flush()
        for logger, handler, old_level, old_propagate in handlers:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
            logger.propagate = old_propagate


def prepare_categorical_file(
    kind: str,
    input_file: str | Path,
    dem_file: str | Path,
    output_file: str | Path,
    lookup_table: str | Path,
    mapping_field: str,
    class_field: str,
    *,
    input_variable: str | None = None,
    is_vector: bool = False,
    classdefinition_file: str | Path | None = None,
    input_crs=None,
    dem_crs=None,
    log: LogCallback | None = None,
) -> Path:
    """Prepare one aligned categorical morphology raster."""
    if kind not in _OUTPUTS:
        raise ValueError("kind must be 'soil', 'geology', 'lc', or 'lai'.")

    formatter_name, raster_name, definition_name = _OUTPUTS[kind]
    if classdefinition_file is not None and definition_name is None:
        raise ValueError("Land-cover formatting does not write a classdefinition.")
    if Path(output_file).suffix.lower() != Path(raster_name).suffix:
        # A GeoTIFF named `.asc` reaches publication as a text grid and fails
        # there instead of here; the name must state the real format.
        raise ValueError(
            f"Categorical {kind} output must be named {Path(raster_name).suffix}, "
            f"not {Path(output_file).suffix}: {output_file}"
        )

    input_path = Path(input_file)
    dem_path = Path(dem_file)
    lookup_path = Path(lookup_table)
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    definition_path = (
        Path(classdefinition_file) if classdefinition_file is not None else None
    )
    if definition_path is not None:
        definition_path.parent.mkdir(parents=True, exist_ok=True)

    from mhm_tools import pre

    formatter = getattr(pre, formatter_name)
    with capture_messages(log), TemporaryDirectory(
        prefix=f"mhm_qgis_{kind}_", dir=output_path.parent
    ) as temporary:
        temporary_path = Path(temporary)
        with lookup.as_csv(lookup_path, dir=temporary_path) as lookup_for_tools:
            formatted_input = input_path
            formatter_mapping_field = mapping_field
            formatter_input_crs = input_crs

            if input_path.suffix.lower() == ".nc" and input_variable:
                from mhm_tools.common.file_handler import (
                    get_raster_data,
                    write_xarray_to_geotiff,
                )

                data = get_raster_data(
                    input_path, var_name=input_variable, crs=input_crs
                )
                try:
                    formatted_input = temporary_path / f"{kind}_selected.tif"
                    write_xarray_to_geotiff(
                        data, formatted_input, data_var=input_variable
                    )
                finally:
                    data.close()
                formatter_input_crs = None

            if is_vector:
                formatted_input = temporary_path / f"{kind}_rasterized.tif"
                # Lookup-based preparation always burns the class field and
                # joins on the mapping field, both named as the user chose
                # them in the lookup table.
                rasterize_kwargs = {
                    "input_file": input_path,
                    "dem_file": dem_path,
                    "output_file": formatted_input,
                    "burn_field": class_field,
                    "lookup_table": lookup_for_tools,
                    "mapping_field": mapping_field,
                }
                if input_crs is not None:
                    rasterize_kwargs["input_crs"] = input_crs
                if dem_crs is not None:
                    rasterize_kwargs["dem_crs"] = dem_crs
                pre.rasterize_map_data(**rasterize_kwargs)
                formatter_mapping_field = class_field
                formatter_input_crs = None

            formatter_kwargs = {
                "input_file": formatted_input,
                "dem_file": dem_path,
                "output_path": temporary_path,
                "lookup_table": lookup_for_tools,
                "mapping_field": formatter_mapping_field,
                "class_field": class_field,
                "output_type": Path(raster_name).suffix.lstrip("."),
            }
            if formatter_input_crs is not None:
                formatter_kwargs["input_crs"] = formatter_input_crs
            if dem_crs is not None:
                formatter_kwargs["dem_crs"] = dem_crs
            formatter(**formatter_kwargs)

            raster_output = temporary_path / raster_name
            if not raster_output.is_file():
                raise FileNotFoundError(
                    f"mhm-tools did not create the expected raster: {raster_output}"
                )

            definition_output = (
                temporary_path / definition_name
                if definition_path is not None and definition_name is not None
                else None
            )
            if definition_output is not None and not definition_output.is_file():
                raise FileNotFoundError(
                    "mhm-tools did not create the expected classdefinition: "
                    f"{definition_output}"
                )

            os.replace(raster_output, output_path)
            if definition_output is not None and definition_path is not None:
                os.replace(definition_output, definition_path)

    return output_path


def prepare_land_cover_periods(
    input_file: str | Path,
    dem_file: str | Path,
    output_path: str | Path,
    lookup_table: str | Path,
    mapping_field: str,
    class_field: str,
    *,
    output_type: str,
    input_crs=None,
    dem_crs=None,
    resampling: str = "auto",
    log: LogCallback | None = None,
) -> tuple[Path, ...]:
    """Format a land-cover period manifest through the public mHM-tools API.

    `input_file` is the `format-data` manifest itself, not its folder:
    mHM-tools reads the CSV and resolves relative scene paths against it.
    """
    from mhm_tools import pre

    output = Path(output_path)
    output.mkdir(parents=True, exist_ok=True)
    lookup_path = Path(lookup_table)
    kwargs = {
        "input_file": Path(input_file),
        "dem_file": Path(dem_file),
        "output_path": output,
        "lookup_table": lookup_path,
        "mapping_field": mapping_field,
        "class_field": class_field,
        "output_type": output_type,
        "resampling": resampling,
    }
    if input_crs is not None:
        kwargs["input_crs"] = input_crs
    if dem_crs is not None:
        kwargs["dem_crs"] = dem_crs
    with lookup.as_csv(
        lookup_path, dir=output, prefix="mhm_qgis_lc_lookup_"
    ) as lookup_csv:
        kwargs["lookup_table"] = lookup_csv
        with capture_messages(log):
            outputs = tuple(Path(path) for path in pre.format_lc_periods(**kwargs))
    return _require_outputs(outputs)


def prepare_soil_horizons(
    input_file: str | Path,
    dem_file: str | Path,
    output_path: str | Path,
    *,
    output_type: str,
    input_crs=None,
    dem_crs=None,
    resampling: str = "auto",
    composition_step: float = 5.0,
    bulk_density_step: float = 0.1,
    log: LogCallback | None = None,
) -> tuple[Path, Path]:
    """Format a soil-horizon manifest through the public mHM-tools API.

    `input_file` is the `format-data` manifest itself, not its folder:
    mHM-tools reads the CSV and resolves relative layer paths against it.
    """
    from mhm_tools import pre

    output = Path(output_path)
    output.mkdir(parents=True, exist_ok=True)
    kwargs = {
        "input_file": Path(input_file),
        "dem_file": Path(dem_file),
        "output_path": output,
        "output_type": output_type,
        "resampling": resampling,
        "composition_step": composition_step,
        "bulkdensity_step": bulk_density_step,
    }
    if input_crs is not None:
        kwargs["input_crs"] = input_crs
    if dem_crs is not None:
        kwargs["dem_crs"] = dem_crs
    with capture_messages(log):
        outputs = tuple(Path(path) for path in pre.format_soil_horizons(**kwargs))
    validated = _require_outputs(outputs)
    if len(validated) != 2:
        raise RuntimeError(
            "mHM-tools did not return soil data and classdefinition outputs."
        )
    return validated[0], validated[1]


def xarray_coord_key(data, *, lat: bool = False, lon: bool = False) -> str:
    """Return the name of a dataset's latitude or longitude coordinate."""
    axis = "Y" if lat else "X"
    for name, coordinate in data.coords.items():
        if (
            coordinate.ndim == 1
            and coordinate.attrs.get("axis", "").upper() == axis
        ):
            return str(name)

    from mhm_tools.common.xarray_utils import get_coord_key

    return get_coord_key(data, lat=lat, lon=lon)


def xarray_single_data_var(dataset) -> str | None:
    """Return the one payload variable of a dataset, or None when ambiguous."""
    from mhm_tools.common.xarray_utils import get_single_data_var

    return get_single_data_var(dataset)


def read_xarray_dataset(file_path, **kwargs):
    """Read a raster or NetCDF into an xarray Dataset."""
    from mhm_tools.common.file_handler import get_xarray_ds_from_file

    return get_xarray_ds_from_file(file_path, **kwargs)


def write_xarray_ascii(dataset, file_path, data_var=None, **kwargs) -> None:
    """Write one xarray payload as an mHM-readable ASCII grid."""
    from mhm_tools.common.file_handler import write_xarray_to_ascii

    write_xarray_to_ascii(dataset, file_path, data_var, **kwargs)


def _require_outputs(outputs: tuple[Path, ...]) -> tuple[Path, ...]:
    if not outputs:
        raise RuntimeError("mHM-tools did not return any formatted outputs.")
    missing = [str(path) for path in outputs if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "mHM-tools did not create the expected output(s): " + ", ".join(missing)
        )
    return outputs


def prepare_lai_file(
    input_file: str | Path,
    dem_file: str | Path,
    output_file: str | Path,
    *,
    output_temporal_resolution: str = "long-term-mean-monthly",
    source_variable: str | None = None,
    dem_crs: str | None = None,
    resampling: str = "bilinear",
    task=None,
    log: LogCallback | None = None,
) -> Path:
    """Stream gridded LAI onto the DEM through mHM-tools."""
    from mhm_tools.pre.format_lai import format_lai_netcdf_file

    output = Path(output_file)
    if output.is_symlink():
        output.unlink()
    with capture_messages(log):
        result = format_lai_netcdf_file(
            input_file,
            dem_file,
            output,
            output_temporal_resolution=output_temporal_resolution,
            source_variable=source_variable,
            dem_crs=dem_crs,
            resampling=resampling,
            is_cancelled=task.isCanceled if task is not None else None,
            progress=task.setProgress if task is not None else None,
            log=log,
        )
    return _require_outputs((Path(result),))[0]


def copy_lai_file_to_grid(
    input_file: str | Path,
    output_file: str | Path,
    target_header: dict,
    crs: str,
    description: str,
    *,
    task=None,
    log: LogCallback | None = None,
) -> Path:
    """Window-copy aligned LAI through mHM-tools."""
    from mhm_tools.pre.format_lai import copy_lai_netcdf_to_grid

    output = Path(output_file)
    if output.is_symlink():
        output.unlink()
    with capture_messages(log):
        result = copy_lai_netcdf_to_grid(
            input_file,
            output,
            target_header,
            crs,
            description,
            is_cancelled=task.isCanceled if task is not None else None,
            progress=task.setProgress if task is not None else None,
            log=log,
        )
    return _require_outputs((Path(result),))[0]


def lai_time_step(output_temporal_resolution: str) -> int:
    """Return the mHM LAI time-step flag through mHM-tools."""
    from mhm_tools.pre.format_lai import lai_time_step as _lai_time_step

    return _lai_time_step(output_temporal_resolution)


def create_dem_derivative_files(
    input_file: str | Path,
    output_folder: str | Path,
    crs: str | None = None,
    *,
    log: LogCallback | None = None,
) -> Path:
    """Create the multi-variable NetCDF derivative dataset through mHM-tools.

    The whole pass is held in memory, so callers that can afford a subprocess
    should run it through `mhm_qgis.core.native_worker` -- see
    `core.handlers.raster.dem_derivative_files`, which injects a worker-backed
    `compute` hook. This function is the one entry point either route uses.
    """
    from mhm_tools.pre.dem_derivatives import create_dem_derivatives

    from ..core.handlers.file.netcdf.raster import file_path

    variable = str(input_file).rsplit(":", 1)[-1] if str(input_file).startswith('NETCDF:"') else None

    folder = Path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    with capture_messages(log):
        written = create_dem_derivatives(
            input_file=str(file_path(input_file)),
            output_path=folder,
            output_extension="nc",
            crs=crs,
            var_name=variable,
        )
    paths = tuple(Path(path) for path in written)
    _require_outputs(paths)
    return paths[0]


def link_folder_tree(
    input_dir: str | Path,
    output_dir: str | Path,
    file_names: tuple[str, ...] = ("*.*",),
    *,
    overwrite: bool = True,
    log: LogCallback | None = None,
) -> tuple[Path, ...]:
    """Symlink files from one folder tree into another through mHM-tools.

    ``file_names`` are the glob patterns mHM-tools matches under ``input_dir``,
    one pass each. Returns the destination paths the patterns resolve to, which
    is what the caller records as prepared output.
    """
    from mhm_tools.pre import link_folder_tree as _link_folder_tree

    source = Path(input_dir)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    linked: list[Path] = []
    with capture_messages(log):
        for pattern in file_names:
            _link_folder_tree(
                source, destination, overwrite=overwrite, file_name=pattern
            )
            linked.extend(
                destination / path.relative_to(source)
                for path in source.rglob(pattern)
            )
    return tuple(linked)


def create_latlon_file(
    out_file: str | Path,
    level0: dict | str | Path,
    level1,
    level11=None,
    level2=None,
    write_header_l0: str | Path | None = None,
    write_header_l1: str | Path | None = None,
    write_header_l11: str | Path | None = None,
    write_header_l2: str | Path | None = None,
    crs: str | None = None,
    dtype: str = "f4",
    compression=None,
    add_bounds: bool = False,
    log: LogCallback | None = None,
) -> Path:
    """Create ``latlon.nc`` through mHM-tools and return its path."""
    from mhm_tools.pre.latlon import create_latlon

    output = Path(out_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        output.unlink()
    with capture_messages(log):
        create_latlon(
            out_file=output,
            level0=level0,
            level1=level1,
            level11=level11,
            level2=level2,
            write_header_l0=write_header_l0,
            write_header_l1=write_header_l1,
            write_header_l11=write_header_l11,
            write_header_l2=write_header_l2,
            crs=crs,
            dtype=dtype,
            compression=compression,
            add_bounds=add_bounds,
        )
    return output


def kling_gupta(simulated, observed, min_valid_pairs=None) -> tuple[float, float, float, float]:
    """KGE (2009) by mhm-tools: (kge, alpha, beta, r); all NaN without a result.

    Post-processing scores use it so they match mhm-tools' evaluations. Importing
    mhm-tools costs about 0.9 s: call it from a coordinator task, never the GUI thread.
    """
    from mhm_tools.common.metrics.kge import calculate_kling_gupta_efficiency

    return calculate_kling_gupta_efficiency(simulated, observed, min_valid_pairs)


def kge_map(simulated, observed, min_valid_pairs=None):
    """Per-cell KGE of two (time, y, x) arrays by mhm-tools; NaN where not computable."""
    import xarray as xr
    from mhm_tools.common.metrics.kge import calculate_kling_gupta_efficiency_per_cell

    dims = ("time", "y", "x")
    result = calculate_kling_gupta_efficiency_per_cell(
        xr.DataArray(simulated, dims=dims), xr.DataArray(observed, dims=dims), min_valid_pairs)
    return result["kge"].to_numpy()


def rmse_map(simulated, observed, min_valid_pairs=None):
    """Per-cell RMSE of two (time, y, x) arrays by mhm-tools."""
    import xarray as xr
    from mhm_tools.common.metrics.rmse import calculate_root_mean_square_error_per_cell

    dims = ("time", "y", "x")
    return calculate_root_mean_square_error_per_cell(
        xr.DataArray(simulated, dims=dims), xr.DataArray(observed, dims=dims), min_valid_pairs).to_numpy()


def pattern_scores(simulated, observed) -> dict:
    """Spatial-pattern efficiency of one map pair (Koch et al. 2018): SPAEF, ESP,
    WASPAEF and MSPAEF by mhm-tools, NaN where a score is undefined."""
    import warnings

    from mhm_tools.common.metrics.esp import ESP
    from mhm_tools.common.metrics.mspaef import MSPAEF
    from mhm_tools.common.metrics.spaef import SPAEF, filter_nan
    from mhm_tools.common.metrics.waspaef import WASPAEF

    scores = {}
    sim, obs = filter_nan(simulated, observed)
    for name, score in (("SPAEF", SPAEF), ("ESP", ESP), ("WASPAEF", WASPAEF), ("MSPAEF", MSPAEF)):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                scores[name] = float(score(sim, obs)[0]) if len(obs) > 2 and obs.std() > 0 else float("nan")
        except (ValueError, ZeroDivisionError, FloatingPointError):
            scores[name] = float("nan")
    return scores


def twsa(model_tws, reference, output_dir, ref_var=None, baseline=(2004, 2009)) -> dict:
    """mhm-tools' TWS-anomaly evaluation of a modelled `tws` file (mm) against a
    reference, anomalies against the `baseline` years; returns its output files.
    Region plots are off."""
    from mhm_tools.post.twsa_evaluation import twsa_evaluation

    return twsa_evaluation(model_tws, reference, output_dir, input_var="tws", ref_var=ref_var,
                           input_format="tws", regions="none",
                           baseline_start_year=baseline[0], baseline_end_year=baseline[1])


def snow_cover(model, reference, output_dir, ref_var=None, threshold=0.0) -> dict:
    """mhm-tools' snow-cover evaluation of mHM's `snowpack` against a reference
    (e.g. MODIS); returns its output files. GIF and region plots are off."""
    from mhm_tools.post.snow_evaluation import snow_evaluation

    return snow_evaluation(model, reference, output_dir, input_var="snowpack", ref_var=ref_var,
                           input_snow_threshold=threshold, write_gif=False, regions="none")


def bankfull(routed_file, out_file, return_period=1.5, var="Qrouted"):
    """Bankfull discharge (`Q_bkfl`) and Lacey's wetted perimeter (`P_bkfl`) from
    mRM's routed discharge, by mhm-tools; returns the output file."""
    from mhm_tools.post.bankfull import bankfull_discharge

    bankfull_discharge(routed_file, out_file, return_period=return_period, wetted_perimeter=True, var=var)
    return out_file


__all__ = [
    "bankfull",
    "capture_messages",
    "copy_lai_file_to_grid",
    "create_dem_derivative_files",
    "create_latlon_file",
    "kge_map",
    "kling_gupta",
    "lai_time_step",
    "link_folder_tree",
    "pattern_scores",
    "prepare_categorical_file",
    "prepare_lai_file",
    "prepare_land_cover_periods",
    "prepare_soil_horizons",
    "read_xarray_dataset",
    "rmse_map",
    "snow_cover",
    "twsa",
    "write_xarray_ascii",
    "xarray_coord_key",
    "xarray_single_data_var",
]

