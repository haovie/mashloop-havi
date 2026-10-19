"""Beat-to-beat distances shared by in-song jumps and cross-song transitions.

Each component distance is divided by its typical within-song value (its
median over random beat pairs), so a combined distance of 1.0 means "as
different as two random beats of the same song" and repeats of the same
material land around 0.1-0.3, whatever the genre."""
import numpy as np
from scipy.spatial.distance import cdist

COMPONENTS = ("chroma", "timbre", "loud", "rhythm", "stems", "vocal", "voice")
# "voice" (who sings, at what register, which syllables) only counts where both
# beats are sung, and it then weighs most: listeners follow the singer first.
WEIGHTS = np.array([1.0, 1.0, 0.4, 0.6, 0.6, 0.4, 1.4])
VOICE = COMPONENTS.index("voice")

# Context window around a splice, in beats relative to the junction: k < 0 is
# what was heard before it, k >= 0 what is heard after.
WINDOW_OFFSETS = np.arange(-4, 4)
WINDOW_WEIGHTS = np.array([0.35, 0.6, 0.85, 1.0, 1.0, 0.85, 0.6, 0.35])


def _cos(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.clip(1.0 - a @ b.T, 0.0, 2.0)


def _voice(fa: dict, fb: dict) -> tuple[np.ndarray, np.ndarray]:
    """Singer distance and the gate (both beats sung, 0..1) it applies under."""
    gate = np.minimum(fa["vocal"][:, None], fb["vocal"][None, :]).astype(np.float64)
    if "vpitch" not in fa or "vpitch" not in fb:
        return np.zeros_like(gate), np.zeros_like(gate)
    # Register: a fifth apart counts as 1; unknown pitch (unvoiced) is neutral.
    dp = np.abs(fa["vpitch"][:, None].astype(np.float64) - fb["vpitch"][None, :]) / 7.0
    dp = np.where(np.isnan(dp), 0.5, np.minimum(dp, 2.0))
    va, vb = fa["vmfcc"], fb["vmfcc"]
    pooled = np.concatenate([va.reshape(-1, va.shape[2]), vb.reshape(-1, vb.shape[2])])
    mu, sd = pooled.mean(0), pooled.std(0) + 1e-6
    za, zb = (va - mu) / sd, (vb - mu) / sd
    dm = 0.5 * (cdist(za[:, 0], zb[:, 0]) + cdist(za[:, 1], zb[:, 1])) / np.sqrt(za.shape[2])
    return 0.5 * dp + 0.5 * dm, gate


def components(fa: dict, fb: dict, mfcc_mu: np.ndarray, mfcc_sd: np.ndarray,
               gain_a: float = 0.0, gain_b: float = 0.0) -> tuple[list[np.ndarray], np.ndarray]:
    """Per-component distance matrices, plus the voice gate."""
    ca, cb = fa["chroma"], fb["chroma"]
    d_chroma = 0.5 * (_cos(ca[:, 0], cb[:, 0]) + _cos(ca[:, 1], cb[:, 1]))
    ma = (fa["mfcc"] - mfcc_mu) / mfcc_sd
    mb = (fb["mfcc"] - mfcc_mu) / mfcc_sd
    d_timbre = 0.5 * (cdist(ma[:, 0], mb[:, 0]) + cdist(ma[:, 1], mb[:, 1])) / np.sqrt(ma.shape[2])
    d_loud = np.abs((fa["loud"] + gain_a)[:, None] - (fb["loud"] + gain_b)[None, :])
    d_rhythm = _cos(fa["rhythm"], fb["rhythm"])
    sa = np.maximum(fa["stems"] + gain_a, -60.0)
    sb = np.maximum(fb["stems"] + gain_b, -60.0)
    d_stems = cdist(sa, sb, "cityblock") / sa.shape[1]
    d_vocal = np.abs(fa["vocal"][:, None] - fb["vocal"][None, :])
    d_voice, gate = _voice(fa, fb)
    return [d_chroma, d_timbre, d_loud, d_rhythm, d_stems, d_vocal, d_voice], gate


def own_scales(f: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MFCC standardisation and component medians for one song."""
    mu = f["mfcc"].reshape(-1, f["mfcc"].shape[2]).mean(0)
    sd = f["mfcc"].reshape(-1, f["mfcc"].shape[2]).std(0) + 1e-6
    comps, gate = components(f, f, mu, sd)
    n = comps[0].shape[0]
    iu = np.triu_indices(n, k=4)
    scales = [max(np.median(c[iu]), 1e-3) for c in comps]
    sung = gate[iu] > 0.5
    # The voice scale is its typical value between two sung beats.
    scales[VOICE] = max(np.median(comps[VOICE][iu][sung]), 1e-3) if sung.sum() > 50 else 1.0
    return mu, sd, np.array(scales)


def combine(comps: list[np.ndarray], scales: np.ndarray, gate: np.ndarray,
            weights: np.ndarray = WEIGHTS) -> np.ndarray:
    """Weighted mean of the scaled components; the voice term joins the mean
    only as far as both beats are sung, so instrumental music keeps its scale."""
    total = np.zeros_like(comps[0])
    den = np.zeros_like(comps[0])
    for k, (c, s, w) in enumerate(zip(comps, scales, weights)):
        wk = w * gate if k == VOICE else w
        total += wk * (c / s)
        den += wk
    return total / den


def drum_presence(f: dict) -> np.ndarray:
    """0..1 per beat: how prominent the drum stem is in the mix."""
    return np.clip((f["stems"][:, 0] - f["loud"] + 20.0) / 15.0, 0, 1)


def crossfade_ms(dp_src: float, dp_dst: float) -> float:
    """Tight drums want a short splice (a long one doubles the hit); sustained
    notes want a longer one so the cut-off ring does not click. Measured with
    tools/eval_splices.py: 12 ms vs 60 ms."""
    dp = max(dp_src, dp_dst)
    return round(12.0 + 48.0 * float(np.clip((0.5 - dp) / 0.5, 0, 1)), 1)


def window(P: np.ndarray, min_coverage: float = 0.6) -> np.ndarray:
    """E[x, y] = weighted mean over k of P[x + k, y + k]: how well the stretch of
    music around x lines up with the stretch around y."""
    n, m = P.shape
    num = np.zeros_like(P)
    den = np.zeros_like(P)
    for k, w in zip(WINDOW_OFFSETS, WINDOW_WEIGHTS):
        x0, x1 = max(0, -k), min(n, n - k)
        y0, y1 = max(0, -k), min(m, m - k)
        if x1 <= x0 or y1 <= y0:
            continue
        num[x0:x1, y0:y1] += w * P[x0 + k:x1 + k, y0 + k:y1 + k]
        den[x0:x1, y0:y1] += w
    E = num / np.maximum(den, 1e-9)
    E[den < min_coverage * WINDOW_WEIGHTS.sum()] = np.inf
    return E


def align_offset(env_a: np.ndarray, ta: float, env_b: np.ndarray, tb: float, rate_hz: float,
                 ibi: float, rate_b: float = 1.0, max_shift: float = 0.02) -> tuple[float, float]:
    """Fine timing correction for a splice: finds the shift of the destination
    that best lines its onset envelope up with what would naturally have played
    after the junction. Returns (shift_seconds, correlation)."""
    taus = np.arange(-0.25 * ibi, 1.0 * ibi, 1.0 / rate_hz)
    ref = np.interp((ta + taus) * rate_hz, np.arange(len(env_a)), env_a)
    ref = ref - ref.mean()
    nref = np.linalg.norm(ref) + 1e-9
    shifts = np.arange(-max_shift, max_shift + 1e-9, 1.0 / rate_hz)
    best, best_r, r0 = 0.0, -1.0, -1.0
    for s in shifts:
        cand = np.interp((tb + s + taus * rate_b) * rate_hz, np.arange(len(env_b)), env_b)
        cand = cand - cand.mean()
        r = float(ref @ cand / (nref * (np.linalg.norm(cand) + 1e-9)))
        if abs(s) < 0.5 / rate_hz:
            r0 = r
        if r > best_r:
            best, best_r = s, r
    # Only trust a shift that clearly beats the analysed grid: on loosely played
    # music small correlation gains are noise (measured: no net improvement).
    if best_r - r0 < 0.06:
        return 0.0, r0
    return float(best), best_r
