"""`step_for_when`: the step of an irregular axis at or before a moment (meteo display)."""

from __future__ import annotations

from datetime import datetime

from mhm_qgis.core import post

# Month starts through 1990, so the spacing is deliberately uneven.
STEPS = [datetime(1990, month, 1) for month in range(1, 7)]


def test_a_moment_maps_to_the_step_at_or_before_it():
    assert post.step_for_when(STEPS, datetime(1990, 1, 1)) == 0
    # Mid-month falls back to the month that contains it, not the next one.
    assert post.step_for_when(STEPS, datetime(1990, 3, 15)) == 2
    assert post.step_for_when(STEPS, datetime(1990, 6, 1)) == 5


def test_moments_outside_the_run_clamp_to_its_ends():
    assert post.step_for_when(STEPS, datetime(1980, 1, 1)) == 0
    assert post.step_for_when(STEPS, datetime(2030, 1, 1)) == 5
    assert post.step_for_when([], datetime(1990, 1, 1)) == 0
