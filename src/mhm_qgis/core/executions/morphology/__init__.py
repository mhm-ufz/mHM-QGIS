"""Public execution plan for morphology preparation."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import commands


@dataclass(frozen=True)
class Stage:
    """One ordered morphology command and its scheduler options."""

    command: str
    label: str = ""
    args: tuple = ()
    options: dict = field(default_factory=dict)
    condition: str = ""



EXECUTE_ALL_STAGES = (
    Stage("dem_derivatives", "DEM derivatives", options={"key": "execute-all-dem"}),
    Stage("gauge_position", "Gauge positions", condition="gauged_outlets"),
    Stage("morph_mask", "Morphology mask"),
    Stage(
        "categorical",
        "Land cover",
        args=("lc",),
        options={"key": "execute-all-lc", "reuse_existing": True},
    ),
    Stage(
        "categorical",
        "Soil",
        args=("soil",),
        options={"key": "execute-all-soil", "reuse_existing": True},
    ),
    Stage(
        "categorical",
        "Geology",
        args=("geology",),
        options={"key": "execute-all-geology", "reuse_existing": True},
    ),
    Stage("lai", "LAI"),
    Stage("publication", "Publish single-domain morphology"),
)


PUBLICATION_STAGES = (
    Stage("publish", "Publish model inputs"),
)


def run_stages(stages, start, *, on_done, on_fail):
    """Run asynchronous stages sequentially through a supplied scheduler."""
    stages = tuple(stages)

    def advance(index=0):
        if index >= len(stages):
            on_done()
            return
        if not start(stages[index], lambda: advance(index + 1)):
            on_fail("Could not start the next preprocessing task.")

    advance()
    return True


# Imported after the plans are defined: ``setup`` consumes
# ``PUBLICATION_STAGES`` as its canonical order.
from . import reset, setup  # noqa: E402 -- after the plan constants they import

__all__ = [
    "EXECUTE_ALL_STAGES",
    "PUBLICATION_STAGES",
    "Stage",
    "commands",
    "reset",
    "run_stages",
    "setup",
]
