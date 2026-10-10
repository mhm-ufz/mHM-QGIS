"""Background work of the Outputs tab, on the shared task coordinator.

Every post job holds the `post-io` resource, so netCDF/HDF5 reads never run in two
of our threads at once (§11); full-file passes go further, into the native worker.
`submit` runs `runner(task)` off the GUI thread and calls `done(result)` on it.
"""
from __future__ import annotations

RESOURCE = "post-io"


def submit(dialog, key, label, runner, done, failed=None) -> bool:
    """Start the job, or, while the same job runs, keep it to run next (latest wins)."""
    pending = dialog._post.widgets.setdefault("pending", {})
    if dialog.task_coordinator.is_busy(f"post:{key}"):
        pending[key] = (label, runner, done, failed)
        return False

    def next_job():
        if key in pending:
            submit(dialog, key, *pending.pop(key))

    def on_error(details):
        last = next((line for line in reversed(str(details).splitlines()) if line.strip()), "")
        dialog.label_postStatus.setText(f"{label} failed")
        dialog.log_message(f"ERROR: {label}: {last}")
        if failed is not None:
            failed(details)
        next_job()

    def on_success(result):
        dialog.label_postStatus.setText("")
        try:
            done(result)
        finally:
            next_job()

    started = dialog.task_coordinator.submit(f"post:{key}", label, runner, on_success=on_success,
                                             on_error=on_error, resource=RESOURCE)
    if started:
        dialog.label_postStatus.setText(f"{label}…")
    return started


def show_progress(dialog) -> None:
    """Mirror the running post job's progress (coordinator `changed`)."""
    running = [row for row in dialog.task_coordinator.snapshots()
               if row["key"].startswith("post:") and row["status"] == "running"]
    dialog.progressBar_postTask.setValue(running[0]["progress"] if running else 0)
