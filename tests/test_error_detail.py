"""The one line a failure dialog shows must name the actual error."""

from mhm_qgis.core.utils.report import error_detail

WORKER_TRACEBACK = """Traceback (most recent call last):
  File "native_worker.py", line 96, in main
    payload = {"result": run(job)}
  File "mhm_tools_handler.py", line 173, in prepare_categorical_file
    pre.rasterize_map_data(**rasterize_kwargs)
TypeError: rasterize_map_data() missing a required argument: 'burn_field'
"""


def test_a_worker_traceback_reports_its_exception_not_its_banner():
    assert error_detail(WORKER_TRACEBACK) == (
        "TypeError: rasterize_map_data() missing a required argument: "
        "'burn_field'"
    )


def test_a_plain_message_is_returned_unchanged():
    assert error_detail("Select a project folder first.") == (
        "Select a project folder first."
    )


def test_a_multi_line_message_keeps_its_first_line():
    assert error_detail("Could not read the DEM.\nCheck the file.") == (
        "Could not read the DEM."
    )


def test_an_exception_is_accepted_directly():
    assert error_detail(ValueError("Select the bulk-density unit.")) == (
        "Select the bulk-density unit."
    )


def test_an_empty_message_stays_empty():
    assert error_detail("") == ""
    assert error_detail("\n  \n") == ""
