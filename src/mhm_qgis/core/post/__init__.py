"""Post-processing engine for finished mHM runs (Qt-free; plan `.development/.post`).

`runs` (current and archived runs), `catalog` (a run's files, variables, intervals),
`grid` (L1 axes, areas, basin weights, sealed fraction), `scan` and `cache` (the one
full-file pass and its products), `series`, `aggregate`, `balance` and `metrics`.
The Outputs tab (`qt/controllers/post_*`) and `qgis_bridge/display/post_canvas`
display what these return.
"""
from __future__ import annotations

import bisect
from datetime import datetime


def step_for_when(steps: list[datetime], when) -> int:
    """Return the index of the step at or before a moment, clamped."""
    if not steps:
        return 0
    if isinstance(when, datetime):
        moment = when
    else:
        moment = datetime(when.year, when.month, when.day)
    position = bisect.bisect_right(steps, moment) - 1
    return max(0, min(position, len(steps) - 1))


__all__ = ["step_for_when"]
