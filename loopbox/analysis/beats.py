"""Beat and downbeat tracking (Beat This!) plus refinement to sub-frame precision.

Beat This! reports times on a 20 ms frame grid. Splicing needs better than that:
two grids off by 10 ms each give an audible flam at the jump. Refinement is two
passes: a local linear fit removes the quantisation jitter wherever the tempo is
steady, then each beat is pulled toward the attack peak of the onset envelope,
with the pull smoothed over neighbouring beats so a missing hit cannot move a
single beat on its own."""
import numpy as np
from scipy.ndimage import median_filter

from . import models
from ..config import SAMPLE_RATE

HR_HOP = 64  # onset envelope hop at 22050 Hz: 2.9 ms


def track(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    beats, downbeats = models.beat_tracker()(y, SAMPLE_RATE)
    beats = np.asarray(beats, dtype=np.float64)
    downbeats = np.asarray(downbeats, dtype=np.float64)
    keep = np.concatenate([[True], np.diff(beats) > 0.15])
    return beats[keep], downbeats


def _smooth_steady(beats: np.ndarray, half: int = 4, max_resid: float = 0.012) -> np.ndarray:
    n = len(beats)
    out = beats.copy()
    for k in range(n):
        lo, hi = max(0, k - half), min(n, k + half + 1)
        if hi - lo < 5:
            continue
        idx = np.arange(lo, hi, dtype=np.float64)
        t = beats[lo:hi]
        A = np.stack([np.ones_like(idx), idx], 1)
        coef, *_ = np.linalg.lstsq(A, t, rcond=None)
        resid = t - A @ coef
        if np.sqrt(np.mean(resid ** 2)) < max_resid:
            out[k] = coef[0] + coef[1] * k
    return out


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    if weights.sum() <= 0:
        return 0.0
    order = np.argsort(values)
    cw = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cw, cw[-1] / 2)])


def refine(beats: np.ndarray, onset_hr: np.ndarray, hr_rate: float,
           search: float = 0.025, half: int = 8) -> np.ndarray:
    beats = _smooth_steady(beats)
    n = len(beats)
    deltas = np.zeros(n)
    strength = np.zeros(n)
    w = int(round(search * hr_rate))
    for k, t in enumerate(beats):
        c = int(round(t * hr_rate))
        lo, hi = max(0, c - w), min(len(onset_hr), c + w + 1)
        if hi - lo < 3:
            continue
        seg = onset_hr[lo:hi]
        p = int(np.argmax(seg))
        # Parabolic interpolation around the peak for sub-hop precision.
        frac = 0.0
        if 0 < p < len(seg) - 1:
            a, b, cc = seg[p - 1], seg[p], seg[p + 1]
            den = a - 2 * b + cc
            if den < 0:
                frac = 0.5 * (a - cc) / den
        deltas[k] = (lo + p + frac) / hr_rate - t
        # How clearly the peak stands out from its window decides how much it counts.
        strength[k] = max(0.0, seg[p] - np.median(seg))
    ref = np.percentile(strength[strength > 0], 75) if np.any(strength > 0) else 1.0
    weights = np.clip(strength / (ref + 1e-9), 0, 1) ** 2
    shift = np.zeros(n)
    for k in range(n):
        lo, hi = max(0, k - half), min(n, k + half + 1)
        shift[k] = _weighted_median(deltas[lo:hi], weights[lo:hi])
    shift = median_filter(shift, size=5, mode="nearest")
    out = beats + shift
    # Never let refinement reorder beats.
    out = np.maximum.accumulate(out)
    return out


def bar_positions(beats: np.ndarray, downbeats: np.ndarray) -> tuple[np.ndarray, int]:
    """Position of each beat inside its bar (0 = downbeat). Beats in bars that are
    longer than the prevailing meter get -1 so they never take part in a jump."""
    n = len(beats)
    if len(downbeats) < 2:
        return np.arange(n) % 4, 4
    db_idx = np.unique(np.clip(np.searchsorted(beats, downbeats - 0.07), 0, n - 1))
    gaps = np.diff(db_idx)
    meter = int(np.bincount(gaps[(gaps >= 2) & (gaps <= 7)]).argmax()) if np.any((gaps >= 2) & (gaps <= 7)) else 4
    pos = np.full(n, -1, dtype=np.int64)
    first = db_idx[0]
    for b in range(first):
        p = meter - (first - b)
        pos[b] = p if p >= 0 else -1
    for s, e in zip(db_idx, list(db_idx[1:]) + [n]):
        for b in range(s, e):
            p = b - s
            pos[b] = p if p < meter else -1
    return pos, meter
