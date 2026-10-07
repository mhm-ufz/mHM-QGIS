"""Goodness of fit on aligned pairs; a pair with NaN on either side is dropped.

KGE (2009) comes from mhm-tools (through the handler, so values match its published
evaluations; importing it costs about 0.9 s, so coordinator tasks only). The rest are
a few numpy lines each, because mhm-tools has no standalone versions.
"""
from __future__ import annotations

import numpy as np

MIN_PAIRS = 10


def pairs(sim, obs):
    sim, obs = np.asarray(sim, "f8").ravel(), np.asarray(obs, "f8").ravel()
    keep = np.isfinite(sim) & np.isfinite(obs)
    return sim[keep], obs[keep]


def nse(sim, obs):
    sim, obs = pairs(sim, obs)
    return float(1 - ((sim - obs) ** 2).sum() / ((obs - obs.mean()) ** 2).sum())


def log_nse(sim, obs):
    """NSE of ln(x + ε), ε = 0.01·mean(obs) (Pushpalatha et al. 2012)."""
    sim, obs = pairs(sim, obs)
    epsilon = 0.01 * obs.mean()
    return nse(np.log(sim + epsilon), np.log(obs + epsilon))


def kge(sim, obs):
    """KGE (2009) and its components: (kge, alpha, beta, r), by mhm-tools."""
    from ...externals.mhm_tools_handler import kling_gupta

    return kling_gupta(*pairs(sim, obs))


def kge_2012(sim, obs):
    """KGE' (Kling et al. 2012): the coefficient of variation ratio replaces alpha."""
    sim, obs = pairs(sim, obs)
    beta = sim.mean() / obs.mean()
    gamma = (sim.std() / sim.mean()) / (obs.std() / obs.mean())
    return float(1 - np.sqrt((pearson(sim, obs) - 1) ** 2 + (beta - 1) ** 2 + (gamma - 1) ** 2))


def pearson(sim, obs):
    sim, obs = pairs(sim, obs)
    return float(np.corrcoef(sim, obs)[0, 1])


def rmse(sim, obs):
    sim, obs = pairs(sim, obs)
    return float(np.sqrt(((sim - obs) ** 2).mean()))


def mae(sim, obs):
    sim, obs = pairs(sim, obs)
    return float(np.abs(sim - obs).mean())


def pbias(sim, obs):
    sim, obs = pairs(sim, obs)
    return float(100 * (sim - obs).sum() / obs.sum())


def volume_error(sim, obs):
    sim, obs = pairs(sim, obs)
    return float(sim.sum() / obs.sum() - 1)


def peak_magnitude_error(sim, obs):
    """(Qs,peak - Qo,peak) / Qo,peak within one event window."""
    sim, obs = pairs(sim, obs)
    return float((sim.max() - obs.max()) / obs.max())


def peak_timing_error(sim, obs, times):
    """t(Qs,peak) - t(Qo,peak) within one event window (times aligned with the series)."""
    sim, obs, times = np.asarray(sim, "f8"), np.asarray(obs, "f8"), np.asarray(times)
    return times[np.nanargmax(sim)] - times[np.nanargmax(obs)]


SCORES = {"NSE": nse, "logNSE": log_nse, "KGE'": kge_2012, "r": pearson, "RMSE": rmse, "MAE": mae,
          "PBIAS": pbias, "volume error": volume_error}


def evaluate(sim, obs, min_pairs=MIN_PAIRS) -> dict:
    """Every score, KGE with its components; NaN with a `reason` below `min_pairs`."""
    count = len(pairs(sim, obs)[0])
    names = [*SCORES, "KGE", "alpha", "beta"]
    if count < min_pairs:
        return {**dict.fromkeys(names, np.nan), "pairs": count,
                "reason": f"{count} paired values; at least {min_pairs} are needed."}
    with np.errstate(invalid="ignore", divide="ignore"):
        result = {name: score(sim, obs) for name, score in SCORES.items()}
    result["KGE"], result["alpha"], result["beta"], _ = kge(sim, obs)
    return {**result, "pairs": count, "reason": ""}
