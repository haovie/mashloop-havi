"""Cross-song transitions between every ordered pair of library tracks.

Three kinds, from most to least seamless:
  cut    after A[i] play B[j]. The music around the junction matches the way an
         in-song jump does. B starts at A's tempo (rate r) and glides back to
         its own over a few bars.
  blend  a DJ mix: from A[a] and B[b] both play for L beats of A, B beat-locked
         to A, with an equal-power crossfade and a bass swap halfway. B may run
         at double or half the beat ratio (m = 2 or 0.5): 70 BPM meets 140.
  fade   a crossfade over a few seconds with no beat matching, placed where the
         drums are quiet. Always possible, so every song stays connected.

Tempo: B comes in at A's tempo (`rate`, up to 8 % off its own) and glides
back to its own; the player time-stretches it with its pitch kept (key lock),
so harmony is compared untransposed and only the recordings' own tunings can
set two songs apart (`keylock` marks the transitions that need the stretch).
A blend may span a wider gap (up to 18 %): A then ramps toward B over the
bars before it (`pre`, also key-locked) and each side covers half.

Key: with key lock B may also be transposed (`semis`, up to two semitones)
so that it sits in A's key for the blend; it glides back to its own pitch
once it plays alone.

Blend styles beyond the regular EQ mix: a filter hand-over, a swap on B's
drop, hand-overs by vocal layers (stem, acapella, a long mashup of B's voice
over A's band), a double drop (both drops at once), and a loop of A laid
under the blend where A has no outro of its own.

Where: hand-overs are scored by where they leave A and enter B, as a DJ reads
a track (structure.roles): leaving as A's section ends (after a chorus or a
drop best), entering B at its intro, a drop or a chorus, and on the 8- and
16-bar lines of a section rather than any bar.
"""
import math
import threading

import numpy as np

from .. import library
from ..config import TRANSITIONS_VERSION
from . import similarity as sim
from .mood import song_mood
from .structure import (_CAMELOT_MAJOR, _CAMELOT_MINOR, PHRASE_4BAR, PHRASE_SECTION, ROLE_BREAK, ROLE_CHORUS,
                        ROLE_DROP, ROLE_INTRO, ROLE_OUTRO)

# Key lock: B is time-stretched with its pitch kept, so it alone absorbs the
# whole tempo gap, up to 8 % (Kim et al. 2020: 86 % of DJs' tempo changes are
# under 5 %, key lock usually on). Below KEYLOCK_FROM a vinyl-style change is
# inaudible (7 cents) and plays without the stretch.
KEYLOCK = True
KEYLOCK_FROM = 0.004
CUT_MAX_RATE = 0.08      # B alone absorbs this much at a cut
BLEND_MAX_RATE = 0.08    # ... and this much in a blend
BEND_MAX = 0.0           # A never bends toward B at a cut
BRIDGE_MAX = 0.08        # the largest gap a cut or a drop closes
# A blend closes a wider one: beyond BLEND_MAX_RATE, A ramps (key-locked) to
# meet B halfway, each side at most 9 % off its own tempo: 100 meets 118 BPM.
BLEND_BRIDGE_MAX = 0.18
# Key shift: with key lock B may be transposed this far to sit in A's key
# (one semitone moves a key seven steps round the Camelot wheel, two
# semitones two steps), only when their keys clash as they are, at a cost per
# semitone: a transposed recording is a little less itself, and it has to
# glide home afterwards.
KEY_SHIFTS = (0, 1, -1, 2, -2)
SHIFT_COST = 0.08
SHIFT_FROM_KP = 0.35  # the key penalty from which B is worth moving (two steps apart or worse)
# The signature styles each keep a place of their own among a pair's blends
# (one each), beside the regular ones: picked on their own merits, not
# crowding out the plain mixes.
SIGNATURE = ("mashup", "double", "loop")
HALF_DOUBLE_PENALTY = 0.06             # a beat-ratio change is felt, if smooth
# A change of song is expected at a cut, so timbre matters less than in-song
# jumps; what gives a cut away is the groove, the level and the arrangement
# breaking. Order: chroma, timbre, loud, rhythm, stems, vocal.
CUT_WEIGHTS = np.array([0.8, 0.35, 0.6, 1.0, 0.8, 0.4, 0.8])  # + voice
# (excellent, acceptable) scores per kind. On the 12-song test library the best
# cut per pair has median 1.18; 0.85 admits ~5 % of pairs, all same-groove funk.
CUT_RANGE = (0.45, 0.85)
BLEND_RANGE = (0.40, 1.05)
FADE_RANGE = (0.60, 1.60)
TAU_CUT = CUT_RANGE[1]
TAU_BLEND = BLEND_RANGE[1]
# Beat-tracking octave slips (a section tracked at 2/3 or 3/2 the tempo) must
# never be used to line two songs up.
MAX_LOCAL_TEMPO_DRIFT = 0.08
MAX_PER_KIND = 4
MIN_LINKS = 3
MIN_IN = 2
FADE_SECONDS = 4.0

# Hard guardrails: what makes a hand-over sound forced, whatever else fits
# (from real DJ mixes, Kim et al. ISMIR 2020; Mosaikbox, Sowula & Knees ISMIR
# 2024; the automatic DJ of Vande Veire & De Bie 2018).
DRUMS_CLASH = 0.35           # both sides' drums above this, unsynced: two kits flamming
VOCAL_OVERLAP_MAX_S = 2.0    # two singers at once for longer than this
BEND_UNDER_VOICE = 0.03      # a vinyl-style tempo bend this large is heard on a voice
DETUNE_MAX_CENTS = 25.0      # keys this far off a semitone grid beat against each other
HARMONIC_DB = 15.0           # harmonic stems (bass, other) within this of the mix count as harmony
LONG_BLEND_VOCAL = 0.2       # 16 and 32-bar blends only where neither side is sung

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def load_features(tid: str) -> dict:
    path = library.features_path(tid)
    mtime = path.stat().st_mtime
    with _cache_lock:
        hit = _cache.get(tid)
        if hit and hit[0] == mtime:
            return hit[1]
    with np.load(path) as z:
        f = {k: z[k] for k in z.files}
    for k in ("meter", "lbp", "key_tonic", "key_mode", "key_camelot"):
        f[k] = int(f[k])
    for k in ("bpm", "gain_db", "env_rate"):
        f[k] = float(f[k])
    # Analysed before these were measured (until it is analysed again): in tune,
    # no section roles, bars counted from the phrase grid.
    f["tuning"] = float(f.get("tuning", 0.0))
    f["key_conf"] = float(f.get("key_conf", 0.0))
    f["cum"] = np.concatenate([[0.0], np.cumsum(f["dur"].astype(np.float64))])
    f["n"] = len(f["beats"])
    if "role" not in f:
        f["role"] = np.zeros(f["n"], np.int8)
    if "bar_in_sec" not in f:
        f["bar_in_sec"] = _bars_from_phrase(f)
    f["level"] = _levels(f)
    f["exit_fit"], f["entry_fit"] = _exit_fit(f), _entry_fit(f)
    f["intensity"] = _intensity(f)
    f["mood"] = _mood(f)
    with _cache_lock:
        if len(_cache) > 64:
            _cache.clear()
        _cache[tid] = (mtime, f)
    return f


def _bars_from_phrase(f: dict) -> np.ndarray:
    """Bar number within the section, from the downbeats since its start."""
    out = np.zeros(f["n"], np.int16)
    bar = -1
    for b in range(f["n"]):
        if f["phrase"][b] == PHRASE_SECTION:
            bar = -1
        if f["pos"][b] == 0:
            bar += 1
        out[b] = max(bar, 0)
    return out


def _levels(f: dict) -> np.ndarray:
    """How strong a line a beat is: 3 on a 16-bar line of its section, 2 on an
    8-bar one, 1 on a 4-bar one, 0 elsewhere (a phrase starts on these)."""
    b = f["bar_in_sec"].astype(int)
    down = f["pos"] == 0
    return np.where(down & (b % 16 == 0), 3, np.where(down & (b % 8 == 0), 2, np.where(down & (b % 4 == 0), 1, 0)))


# Weight of where a hand-over leaves and enters against its other costs (DJs
# and automatic mixers alike place them on section boundaries; Spotify's looks
# for its exit late in A and its entry early in B, Bittner et al. 2017).
FIT_WEIGHT = 0.3


def _exit_fit(f: dict) -> np.ndarray:
    """Per beat x, leaving A so that x would be its next beat: best as A's
    section ends (above all after a chorus or a drop), on a strong line."""
    sec_end = (f["phrase"] == PHRASE_SECTION).astype(float)
    prev_role = np.r_[f["role"][:1], f["role"][:-1]]
    after_peak = np.isin(prev_role, (ROLE_CHORUS, ROLE_DROP)) * sec_end
    return 0.5 * sec_end + 0.2 * f["level"] / 3 + 0.3 * after_peak


def _entry_fit(f: dict) -> np.ndarray:
    """Per beat y, entering B there: best at a section start, above all its
    intro, a drop or a chorus, on a strong line."""
    start = (f["phrase"] == PHRASE_SECTION).astype(float)
    good = np.isin(f["role"], (ROLE_INTRO, ROLE_DROP, ROLE_CHORUS))
    return 0.6 * start * np.where(good, 1.0, 0.6) + 0.2 * f["level"] / 3


def _intensity(f: dict) -> np.ndarray:
    """0..1 per beat, smoothed over two bars: how full and driving the music is
    there (the drums, and the level relative to the song's own loudest part).
    A hand-over between equal intensities is felt as continuous; a jump from a
    full chorus into a quiet intro, or back, is felt as a jolt."""
    from scipy.ndimage import uniform_filter1d
    rel = np.clip((f["loud"] - f["loud"].max() + 18.0) / 18.0, 0, 1)
    raw = 0.5 * rel + 0.5 * sim.drum_presence(f)
    return uniform_filter1d(raw.astype(np.float64), size=2 * f["meter"] + 1, mode="nearest")


def _mood(f: dict) -> dict:
    return song_mood(f, f["bpm"], f["key_mode"])


# Spread of song brightness in a typical library; fixes the scale of that term.
BRIGHT_SCALE = 15.0


def mood_distance(fa: dict, fb: dict) -> float:
    """About 0.1 for two songs of one style (two funk tracks), 0.6 for the
    median pair, 1.5-2 for a piano ballad against a dance track."""
    a, b = fa["mood"], fb["mood"]
    raw = (1.2 * abs(a["drums"] - b["drums"]) + 1.6 * abs(a["fold"] - b["fold"])
           + 0.5 * abs(a["bright"] - b["bright"]) / BRIGHT_SCALE + 0.25 * abs(a["major"] - b["major"])
           + 0.4 * abs(a["vocal"] - b["vocal"]) + 0.03 * abs(a["dyn"] - b["dyn"]))
    return raw / 2.0


def _key_penalty(fa: dict, fb: dict, semis: int = 0) -> tuple[float, int]:
    """Camelot distance between A and B, with B transposed by `semis` (what
    playing it faster or slower does to its pitch)."""
    tonic = (fb["key_tonic"] + semis) % 12
    cam_b = (_CAMELOT_MAJOR if fb["key_mode"] == 0 else _CAMELOT_MINOR)[tonic]
    d = abs(fa["key_camelot"] - cam_b) % 12
    d = min(d, 12 - d) + (1 if fa["key_mode"] != fb["key_mode"] else 0)
    return {0: 0.0, 1: 0.1, 2: 0.35}.get(d, 0.7), d


def _bridge(R: np.ndarray, alone: float) -> tuple[np.ndarray, np.ndarray]:
    """Split a tempo gap. R is the rate B needs with A untouched. Returns A's
    rate at the junction and B's start rate: within `alone` B takes it all,
    beyond it each side takes half (in log terms)."""
    lr = np.log(R)
    pre = np.where(np.abs(lr) <= math.log(1 + alone), 1.0, np.exp(-lr / 2))
    return pre, R * pre


def _semis(R) -> np.ndarray:
    """Semitones B sounds shifted relative to A when it runs R times as fast:
    none with key lock; vinyl-style, 12 * log2(R) rounded."""
    if KEYLOCK:
        return np.zeros(np.shape(R), dtype=int)
    return np.rint(12 * np.log2(R)).astype(int)


def _keylocked(rate: float) -> bool:
    """Whether B must be time-stretched (pitch kept) to come in at this rate."""
    return bool(KEYLOCK and abs(math.log(rate)) > math.log(1 + KEYLOCK_FROM))


def _detune(R, tune_a: float = 0.0, tune_b: float = 0.0) -> np.ndarray:
    """Cents B sounds off the nearest semitone of A: the two recordings' own
    tunings, plus (vinyl-style, or key lock's tiny unstretched changes) what
    running R times as fast does; 3 % faster on vinyl is half a semitone."""
    lr = np.log(np.asarray(R, dtype=np.float64))
    shift = 1200 * lr / math.log(2)
    if KEYLOCK:
        shift = np.where(np.abs(lr) > math.log(1 + KEYLOCK_FROM), 0.0, shift)
    cents = shift + (tune_b - tune_a)
    return np.abs(cents - 100 * np.rint(cents / 100))


def harmonic_presence(f: dict) -> np.ndarray:
    """True per beat where the pitched stems (bass, other) carry real weight in
    the mix: there, two songs out of tune with each other are heard."""
    return np.maximum(f["stems"][:, 1], f["stems"][:, 2]) > f["loud"] - HARMONIC_DB


def _best_shift(fa: dict, fb: dict, ha: np.ndarray, hb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For every pair of regions (ha: A's mean chroma per region, hb: B's, both
    unit-norm), the transposition of B that fits A best in key and harmony.
    Returns (semis, d_h, kp): the shift, the harmony distance and the key
    penalty with B moved by it. Without key lock, or when the keys already sit
    together, B is never moved."""
    best = None
    shifts = KEY_SHIFTS if KEYLOCK and _key_penalty(fa, fb, 0)[0] >= SHIFT_FROM_KP else (0,)
    for s in shifts:
        dh = 1.0 - ha @ np.roll(hb, s, axis=1).T
        kp = _key_penalty(fa, fb, s)[0]
        cost = kp + 0.6 * dh + SHIFT_COST * abs(s)
        if best is None:
            best = [cost, np.zeros(cost.shape, int), dh, np.full(cost.shape, kp)]
            continue
        m = cost < best[0]
        best[0] = np.where(m, cost, best[0])
        best[1] = np.where(m, s, best[1])
        best[2] = np.where(m, dh, best[2])
        best[3] = np.where(m, kp, best[3])
    return best[1], best[2], best[3]


def _tempo_penalty(lr: np.ndarray, alone: float, scale: float, bridge: float = BRIDGE_MAX) -> np.ndarray:
    """Small for gaps B absorbs alone, then rising as both songs must bend."""
    a = math.log(1 + alone)
    full = math.log(1 + bridge)
    small = scale * np.minimum(lr, a) / a
    if full <= a:
        return small
    return small + np.where(lr > a, 0.12 + 0.28 * (lr - a) / (full - a), 0.0)


def _pooled_mfcc(fa: dict, fb: dict):
    m = np.concatenate([fa["mfcc"].reshape(-1, fa["mfcc"].shape[2]), fb["mfcc"].reshape(-1, fb["mfcc"].shape[2])])
    return m.mean(0), m.std(0) + 1e-6


def _nms(cands: list[dict], key: str, min_sep: int, limit: int) -> list[dict]:
    out = []
    for c in sorted(cands, key=lambda c: c["score"]):
        if all(abs(c[key] - o[key]) >= min_sep for o in out):
            out.append(c)
            if len(out) >= limit:
                break
    return out


def _quality(score: float, rng: tuple[float, float]) -> float:
    lo, hi = rng
    return round(float(np.clip(0.4 + 0.6 * (hi - score) / (hi - lo), 0.05, 1.0)), 3)


def _ahead(x: np.ndarray, n: int) -> np.ndarray:
    """Mean of x over the n beats starting at each beat (fewer at the end)."""
    c = np.concatenate([[0.0], np.cumsum(x, dtype=np.float64)])
    i = np.arange(len(x))
    j = np.minimum(i + n, len(x))
    return (c[j] - c[i]) / np.maximum(1, j - i)


def _behind(x: np.ndarray, n: int) -> np.ndarray:
    """Mean of x over the n beats before each beat (fewer at the start)."""
    c = np.concatenate([[0.0], np.cumsum(x, dtype=np.float64)])
    i = np.arange(len(x))
    j = np.maximum(i - n, 0)
    return np.where(i > j, (c[i] - c[j]) / np.maximum(1, i - j), x[0] if len(x) else 0.0)


def _bent_voice(pre: np.ndarray, rb: np.ndarray, voice_before_a: np.ndarray, voice_b: np.ndarray) -> np.ndarray:
    """True where a vinyl-style bend would be heard on a singer: A bending on
    its way in, or B arriving off its own tempo while someone sings (never
    with key lock: the pitch stays, and a voice stretched up to 8 % is fine)."""
    if KEYLOCK:
        return np.zeros(np.broadcast(pre, voice_before_a, voice_b).shape, dtype=bool)
    big = math.log(1 + BEND_UNDER_VOICE)
    return (((np.abs(np.log(pre)) > big) & (voice_before_a > 0.3))
            | ((np.abs(np.log(rb)) > big) & (voice_b > 0.3)))


def _rise_fall(ia: np.ndarray, ib: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How far the intensity goes up and down from A (rows) to B (columns)."""
    d = ib[None, :] - ia[:, None]
    return np.maximum(d, 0.0), np.maximum(-d, 0.0)


def _steady(f: dict, ibi: np.ndarray) -> np.ndarray:
    return np.abs(np.log(ibi / np.median(f["dur"]))) <= math.log(1 + MAX_LOCAL_TEMPO_DRIFT)


BEAT_EVENNESS = 0.22  # a beat inside a blend window may differ this much from the window's mean


def _even(f: dict, starts: np.ndarray, length: int) -> np.ndarray:
    """True for windows whose every beat is close to the window's mean beat. A
    steady mean can hide one long beat (a pause, a beat the tracker missed):
    the other song, locked beat by beat, would fall a beat out of step there."""
    d = _gather(f["dur"], starts, length)
    return (np.abs(np.log(d / d.mean(1, keepdims=True))) <= math.log(1 + BEAT_EVENNESS)).all(1)


def cuts(fa: dict, fb: dict, to: str) -> list[dict]:
    if fa["meter"] != fb["meter"]:
        return []
    meter = fa["meter"]
    mu, sd = _pooled_mfcc(fa, fb)
    na, nb = fa["n"], fb["n"]
    x = np.arange(na)[:, None]
    y = np.arange(nb)[None, :]
    rate = fb["local_ibi"][None, :] / fa["local_ibi"][:, None]
    lr = np.abs(np.log(rate))
    aud_a = fa["loud"] > fa["loud"].max() - 30
    aud_b = fb["loud"] > fb["loud"].max() - 30
    # Only on phrase starts on both sides: a new song arriving on the "one" of
    # a phrase sounds like arrangement, anywhere else like an edit.
    valid = ((fa["phrase"][:, None] >= PHRASE_4BAR) & (fb["phrase"][None, :] >= PHRASE_4BAR) & (x >= 4)
             & (y <= fb["lbp"] - 2 * meter) & (y >= 1) & (lr <= math.log(1 + BRIDGE_MAX))
             & aud_a[:, None] & aud_b[None, :]
             & _steady(fa, fa["local_ibi"])[:, None] & _steady(fb, fb["local_ibi"])[None, :])
    pre_m, rb_m = _bridge(rate, CUT_MAX_RATE)
    valid &= ~_bent_voice(pre_m, rb_m, _behind(fa["vocal"], 16)[:, None], _ahead(fb["vocal"], 8)[None, :])
    if not valid.any():
        return []
    # B sounds transposed by the rate it runs at, so harmony is compared with
    # its chroma rolled by those semitones (one similarity pass per shift).
    semis = _semis(rate)
    scales = 0.5 * (fa["scales"] + fb["scales"])
    S = np.full((na, nb), np.inf)
    for s in np.unique(semis[valid]):
        fbs = fb if s == 0 else {**fb, "chroma": np.roll(fb["chroma"], int(s), axis=2)}
        comps, gate = sim.components(fa, fbs, mu, sd, fa["gain_db"], fb["gain_db"])
        P = sim.combine(comps, scales, gate, CUT_WEIGHTS)
        E = sim.window(P)
        Pm1 = np.full_like(P, np.inf)
        Pm1[1:, 1:] = P[:-1, :-1]
        J = np.maximum(P, Pm1)
        mask = valid & (semis == s)
        with np.errstate(invalid="ignore"):
            S[mask] = (E + 0.3 * np.maximum(0.0, J - E))[mask]
    va, vb = fa["vocal"], fb["vocal"]
    va_prev = np.r_[0.0, va[:-1]]
    vb_prev = np.r_[0.0, vb[:-1]]
    # Cutting while someone is mid-line gives the singer away.
    pen_v = 0.5 * (va_prev * va)[:, None] + 0.3 * (vb_prev * vb)[None, :]
    pen_t = _tempo_penalty(lr, CUT_MAX_RATE, 0.15)
    bonus = FIT_WEIGHT * (fa["exit_fit"][:, None] + fb["entry_fit"][None, :])
    # Nothing overlaps at a cut to soften it: intensity and mood must carry on.
    ia_prev = np.r_[fa["intensity"][0], fa["intensity"][:-1]]
    jolt = np.abs(ia_prev[:, None] - fb["intensity"][None, :])
    S = S + pen_v + pen_t - bonus + 1.0 * jolt + 0.8 * mood_distance(fa, fb)
    S = np.where(valid & np.isfinite(S), S, np.inf)
    flat = np.argsort(S, axis=None)[:200]
    cands = []
    for f in flat:
        xi, yi = divmod(int(f), nb)
        s = float(S[xi, yi])
        if not np.isfinite(s) or s > TAU_CUT:
            break
        cands.append({"x": xi, "y": yi, "score": s})
    out = []
    ibi = float(np.median(fa["dur"]))
    dpa, dpb = sim.drum_presence(fa), sim.drum_presence(fb)
    for c in _nms(cands, "x", 4 * meter, MAX_PER_KIND):
        xi, yi = c["x"], c["y"]
        r = float(rate[xi, yi])
        pre, rb = (float(v) for v in _bridge(np.array(r), CUT_MAX_RATE))
        shift, _ = sim.align_offset(fa["onset_env"], fa["beats"][xi], fb["onset_env"], fb["beats"][yi],
                                    fa["env_rate"], ibi, rate_b=r)
        out.append({"type": "cut", "to": to, "i": xi - 1, "j": yi, "rate": round(rb, 5), "pre": round(pre, 5),
                    "semis": int(semis[xi, yi]), "m": 1, "keylock": _keylocked(rb),
                    "shift_ms": round(shift * 1000, 2), "score": round(c["score"], 4),
                    "q": _quality(c["score"], CUT_RANGE),
                    "xf_ms": sim.crossfade_ms(max(dpa[xi - 1], dpa[xi]), max(dpb[yi - 1], dpb[yi]))})
    return out


def _region_starts(f: dict, length: int, end_limit: int, include_first: bool) -> np.ndarray:
    idx = np.where(f["phrase"] >= PHRASE_4BAR)[0]
    if include_first:
        first = np.where(f["pos"] == 0)[0][:1]
        idx = np.union1d(idx, first)
    return idx[idx + length <= end_limit]


def _gather(arr: np.ndarray, starts: np.ndarray, length: int) -> np.ndarray:
    return arr[starts[:, None] + np.arange(length)[None, :]]


def _pool_pairs(rows: np.ndarray) -> np.ndarray:
    """Two consecutive beat rhythm profiles (n, 2, D) squeezed into one beat's
    worth (n, D): what a double-time beat pair looks like on a half-time grid."""
    n, _, d = rows.shape
    x = rows.reshape(n, 2 * d).reshape(n, d, 2).mean(2)
    return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9)


def _rhythm_regions(fa, fb, A, B, L, m):
    """Per-beat rhythm profiles of both regions on A's beat grid, flattened."""
    if m == 1:
        ra = _gather(fa["rhythm"], A, L)
        rb = _gather(fb["rhythm"], B, L)
    elif m == 2:
        ra = _gather(fa["rhythm"], A, L)
        g = fb["rhythm"][B[:, None] + np.arange(2 * L)[None, :]]          # (nB, 2L, D)
        rb = np.stack([_pool_pairs(g[:, 2 * q:2 * q + 2]) for q in range(L)], 1)
    else:  # m == 0.5: two A beats per B beat; compare on B's grid
        g = fa["rhythm"][A[:, None] + np.arange(L)[None, :]]
        ra = np.stack([_pool_pairs(g[:, 2 * q:2 * q + 2]) for q in range(L // 2)], 1)
        rb = _gather(fb["rhythm"], B, L // 2)
    n = ra.shape[1]
    return ra.reshape(len(A), -1), rb.reshape(len(B), -1), n


def blends(fa: dict, fb: dict, to: str, stems: bool = False) -> list[dict]:
    if fa["meter"] != fb["meter"]:
        return []
    meter = fa["meter"]
    mu, sd = _pooled_mfcc(fa, fb)
    harm_a, harm_b = harmonic_presence(fa), harmonic_presence(fb)
    md = mood_distance(fa, fb)
    drop_a = (fa["role"] == ROLE_DROP) & (fa["phrase"] == PHRASE_SECTION)
    drop_b = (fb["role"] == ROLE_DROP) & (fb["phrase"] == PHRASE_SECTION)
    cands = []
    for m in (1, 2, 0.5):
        # Two bars slip a blend into the breath between two sung lines; four
        # and eight bars are the classic DJ lengths; sixteen and thirty-two are
        # how DJs mix instrumental intros and outros (real mixes peak at
        # multiples of 32 beats), only where neither side is sung (or as a
        # mashup, the voice of one taken out by its stem).
        for L in (2 * meter, 4 * meter, 8 * meter, 16 * meter, 32 * meter):
            span = int(round(m * L))  # B beats covered while A plays L
            A = _region_starts(fa, L, fa["n"], False)
            A = A[A >= 2 * meter]
            B = _region_starts(fb, span, min(fb["n"], fb["lbp"]), True)
            if len(A) == 0 or len(B) == 0:
                continue
            dur_a = fa["cum"][A + L] - fa["cum"][A]
            dur_b = fb["cum"][B + span] - fb["cum"][B]
            rate = dur_b[None, :] / dur_a[:, None]
            lr = np.abs(np.log(rate))
            ok = ((lr <= math.log(1 + BLEND_BRIDGE_MAX))
                  & _steady(fa, dur_a / L)[:, None] & _steady(fb, dur_b / span)[None, :]
                  & _even(fa, A, L)[:, None] & _even(fb, B, span)[None, :])
            va_win = _gather(fa["vocal"], A, L).mean(1)
            vb_win = _gather(fb["vocal"], B, span).mean(1)
            # Keys off the semitone grid where both songs carry harmony.
            both_harm = (_gather(harm_a, A, L).mean(1) > 0.5)[:, None] & (_gather(harm_b, B, span).mean(1) > 0.5)[None, :]
            ok &= ~(both_harm & (_detune(rate, fa["tuning"], fb["tuning"]) > DETUNE_MAX_CENTS))
            if not ok.any():
                continue
            k = np.arange(L)
            ga, gb = np.cos(0.5 * np.pi * k / L), np.sin(0.5 * np.pi * k / L)
            w = ga * gb
            kb = np.floor(m * k).astype(int)  # B beat under each A beat
            vb = fb["vocal"][B[:, None] + kb[None, :]]
            if m == 2:
                vb = 0.5 * (vb + fb["vocal"][B[:, None] + kb[None, :] + 1])
            clash = (_gather(fa["vocal"], A, L) * w) @ vb.T / w.sum()
            # Seconds both sing at once while both are within 10 dB of each
            # other (from 5 % to 90 % of the window with the player's curves).
            mid = (k >= round(0.05 * L)) & (k < round(0.9 * L))
            sung_a = (_gather(fa["vocal"], A, L) > 0.5) * mid * (dur_a / L)[:, None]
            both_s = sung_a @ (vb > 0.5).T
            # Two singers at once: only with both vocal stems, through a stem
            # hand-over (B's voice held back until A's is taken out).
            voc_bad = both_s > VOCAL_OVERLAP_MAX_S
            if not stems:
                ok &= ~voc_bad
            if not ok.any():
                continue
            ha = _gather(fa["chroma"].mean(1), A, L).mean(1)
            hb = _gather(fb["chroma"].mean(1), B, span).mean(1)
            ha /= np.linalg.norm(ha, axis=1, keepdims=True) + 1e-9
            hb /= np.linalg.norm(hb, axis=1, keepdims=True) + 1e-9
            # Harmony and key with B transposed to fit A best (key lock).
            semis, d_h, kp = _best_shift(fa, fb, ha, hb)
            # Keys two or more steps apart on the Camelot wheel never share a
            # blend where both carry harmony: those songs meet by a cut, an
            # echo or a wash instead.
            ok &= ~(both_harm & (kp >= 0.35))
            if not ok.any():
                continue
            ra, rb, nr = _rhythm_regions(fa, fb, A, B, L, m)
            d_r = 1.0 - ra @ rb.T / nr
            ta = ((_gather(fa["mfcc"].mean(1), A, L).mean(1)) - mu) / sd
            tb = ((_gather(fb["mfcc"].mean(1), B, span).mean(1)) - mu) / sd
            d_t = np.sqrt(((ta[:, None, :] - tb[None, :, :]) ** 2).sum(2)) / np.sqrt(ta.shape[1])
            pre_l = np.array([fa["loud"][max(0, a - 2 * meter):a].mean() for a in A]) + fa["gain_db"]
            post_l = np.array([fb["loud"][b + span:min(fb["n"], b + span + 2 * meter)].mean() if b + span < fb["n"]
                               else fb["loud"][-1] for b in B]) + fb["gain_db"]
            pen_e = 0.04 * np.maximum(0.0, np.abs(pre_l[:, None] - post_l[None, :]) - 2.0)
            # Where it leaves (the blend ends as A's section does) and enters
            # (B's intro, a section start); a long mix belongs to A's outro or
            # breakdown laid over B's intro.
            end_a = np.minimum(A + L, fa["n"] - 1)
            bonus = FIT_WEIGHT * (fa["exit_fit"][end_a][:, None] + fb["entry_fit"][B][None, :])
            if L >= 16 * meter:
                a_out = np.isin(_gather(fa["role"], A, L), (ROLE_OUTRO, ROLE_BREAK)).mean(1) > 0.5
                bonus = bonus + 0.1 * (a_out[:, None] & (fb["role"][B] == ROLE_INTRO)[None, :])
            pen_t = (_tempo_penalty(lr, BLEND_MAX_RATE, 0.25, BLEND_BRIDGE_MAX)
                     + (HALF_DOUBLE_PENALTY if m != 1 else 0.0))
            # Longer blends need cleaner material; short ones are a little rougher.
            base = {32 * meter: 0.2, 16 * meter: 0.22, 8 * meter: 0.25, 4 * meter: 0.3}.get(L, 0.36)
            # Where B is left playing alone should feel like where A was (the
            # styles made to lift a set, below, may rise freely).
            b_after = np.minimum(B + span, fb["n"] - 1)
            rise, fall = _rise_fall(fa["intensity"][A], fb["intensity"][b_after])
            jolt = rise + fall
            S = (base + kp + 0.6 * d_h + 1.2 * clash + pen_e + 0.4 * d_r + 0.2 * d_t + pen_t - bonus
                 + 0.4 * jolt + 0.35 * md + SHIFT_COST * np.abs(semis))
            S0 = np.where(ok, S, np.inf)
            # Long windows with the whole of both songs only where neither is sung.
            if L >= 16 * meter:
                quiet = (va_win < LONG_BLEND_VOCAL)[:, None] & (vb_win < LONG_BLEND_VOCAL)[None, :]
            else:
                quiet = np.ones(S.shape, bool)
            plain = quiet & ~voc_bad
            # One style per window, the cheapest that fits:
            #   eq/long/short  the regular EQ blend, never two singers at once;
            #   filter  A high-passed out, B low-passed in: two songs that do
            #           not sit together harmonically (4 and 8 bars);
            #   swap    the window ends on B's drop: B builds up thin under A,
            #           and on the drop A is cut (8 and 16 bars);
            #   stem    two singers at once made possible by the vocal stems:
            #           B's voice held back until A's goes;
            #   acapella  B's sung window over A's instrumental, then B's own
            #           band (8 bars, keys that sit together);
            #   mashup  the same at length: B's singer over A's band without
            #           its voice for 16 or 32 bars, B's band only at the end;
            #   double  both drops at once: B's build-up thin under A's, then
            #           the second half on both songs' drops, B's bass alone.
            styles = {"long" if L >= 16 * meter else "eq" if L == 8 * meter else "short": np.where(plain, S0, np.inf)}
            special = {}  # the signature styles, each scored on its own
            if L in (4 * meter, 8 * meter):
                # A filter hand-over is also how a DJ crosses a change of energy.
                apart = ((kp >= 0.35) | (d_h > 0.4)) & (lr <= math.log(1.03))
                styles["filter"] = np.where(apart & ~voc_bad, S0 - 0.4 * kp - 0.2 * d_h + 0.08 - 0.2 * jolt, np.inf)
            if L in (8 * meter, 16 * meter):
                end_b = B + span
                on_drop = np.zeros(len(B), bool)
                inside = end_b < fb["n"]
                on_drop[inside] = drop_b[end_b[inside]]
                styles["swap"] = np.where(on_drop[None, :] & plain, S0 - 0.15 - 0.4 * rise, np.inf)
                if m == 1:
                    half = L // 2
                    both_drop = drop_a[A + half][:, None] & drop_b[B + half][None, :]
                    fits = both_drop & plain & (kp <= 0.1) & (d_h < 0.35) & (d_r < 0.35)
                    special["double"] = np.where(fits, S0 - 0.04 - 0.4 * rise, np.inf)
            if stems:
                styles["stem"] = np.where(voc_bad & quiet, S0 - 1.2 * clash + 0.05, np.inf)
                if L == 8 * meter:
                    sings = (vb_win > 0.5)[None, :] & (va_win > 0.3)[:, None]
                    styles["acapella"] = np.where(sings & (kp <= 0.1) & (d_h < 0.35), S0 - 1.2 * clash, np.inf)
                if L >= 16 * meter and m == 1:
                    # B sings most of the window, over an A band that carries on.
                    alive = _gather(fa["intensity"], A, L).min(1) > 0.25
                    sings = (vb_win > 0.55)[None, :] & alive[:, None]
                    special["mashup"] = np.where(sings & (kp <= 0.1) & (d_h < 0.3), S0 - 1.2 * clash, np.inf)

            def collect(S, style_at, limit):
                for f in np.argsort(S, axis=None)[:limit]:
                    ai, bi = divmod(int(f), len(B))
                    s = float(S[ai, bi])
                    if not np.isfinite(s) or s > TAU_BLEND:
                        break
                    style = style_at(ai, bi)
                    cands.append({"a": int(A[ai]), "b": int(B[bi]), "len": L, "m": m, "R": float(rate[ai, bi]),
                                  "semis": int(semis[ai, bi]), "score": s, "clash": float(clash[ai, bi]),
                                  "style": style, "stems": style in ("stem", "acapella", "mashup")})

            names = list(styles)
            stack = np.stack([styles[s] for s in names])
            best_style = stack.argmin(0)
            collect(stack.min(0), lambda ai, bi: names[best_style[ai, bi]], 150)
            for name, Sx in special.items():
                collect(Sx, lambda ai, bi, name=name: name, 40)
    cands += _loop_cands(fa, fb, stems, mu, sd, harm_a, harm_b, md)
    picked = _nms([c for c in cands if c["style"] not in SIGNATURE], "a", 4 * meter, MAX_PER_KIND)
    for style in SIGNATURE:
        picked += _nms([c for c in cands if c["style"] == style], "a", 4 * meter, 1)
    out = []
    ibi = float(np.median(fa["dur"]))
    for c in picked:
        pre, rb = (float(v) for v in _bridge(np.array(c["R"]), BLEND_MAX_RATE))
        shift, _ = sim.align_offset(fa["onset_env"], fa["beats"][c["a"]], fb["onset_env"], fb["beats"][c["b"]],
                                    fa["env_rate"], ibi, rate_b=c["R"])
        item = {"type": "blend", "to": to, "a": c["a"], "b": c["b"], "len": c["len"], "m": c["m"],
                "style": c["style"], "rate": round(rb, 5), "pre": round(pre, 5), "semis": c["semis"],
                "keylock": _keylocked(rb) or c["semis"] != 0, "stems": c["stems"],
                "shift_ms": round(shift * 1000, 2),
                "score": round(c["score"], 4), "q": _quality(c["score"], BLEND_RANGE),
                "clash": round(c["clash"], 3)}
        if c.get("loop"):
            item["loop"] = c["loop"]
        out.append(item)
    return out


# Loop outros: a loop of one, two or four bars of A, from a downbeat on a phrase
# line, repeated under the whole blend while B comes in, for the songs that
# end without an outro to mix over (most pop). A loop never repeats more than
# eight times (a one-bar loop for eight bars, a two-bar one for sixteen).
LOOP_BARS = (1, 2, 4)
LOOP_MAX_REPEATS = 8
LOOP_SEAM_MAX = 0.4   # a loop that closes worse than this is heard as a skip
LOOP_COST = 0.1       # A's own music, where it fits as well, beats a loop of it
# A loop with A's voice taken out by its stem closes on the band alone.
_SEAM_WEIGHTS_BAND = sim.WEIGHTS * (np.array(sim.COMPONENTS) != "vocal") * (np.array(sim.COMPONENTS) != "voice")


def _diag_window(P: np.ndarray, n: int) -> np.ndarray:
    """sim.window(P)[y + n, y] for every beat y (inf where context is short):
    how a jump from the end of an n-beat loop back to its start at y scores."""
    N = P.shape[0]
    d = np.zeros(N)
    d[:N - n] = P[np.arange(n, N), np.arange(N - n)]  # d[i] = P[i + n, i]
    num, den = np.zeros(N), np.zeros(N)
    for k, w in zip(sim.WINDOW_OFFSETS, sim.WINDOW_WEIGHTS):
        idx = np.arange(N) + k
        valid = (idx >= 0) & (idx < N - n)
        num[valid] += w * d[idx[valid]]
        den[valid] += w
    E = num / np.maximum(den, 1e-9)
    E[den < 0.6 * sim.WINDOW_WEIGHTS.sum()] = np.inf
    return E


def _seams(f: dict, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Per beat y, how well an n-beat loop from y closes on itself, scored as
    an in-song jump from its end back to y: with the voice (full) and on the
    band alone. Worked out once per song, for every loop length."""
    if "_seams" not in f:
        comps, gate = sim.components(f, f, f["mfcc_mu"], f["mfcc_sd"])
        full = sim.combine(comps, f["scales"], gate)
        band = sim.combine(comps, f["scales"], gate, _SEAM_WEIGHTS_BAND)
        f["_seams"] = {b * f["meter"]: (_diag_window(full, b * f["meter"]), _diag_window(band, b * f["meter"]))
                       for b in LOOP_BARS}
    return f["_seams"][n]


def _loop_cands(fa: dict, fb: dict, stems: bool, mu, sd, harm_a, harm_b, md: float) -> list[dict]:
    """Blend candidates over a loop of A (style "loop"). The loop must close
    on itself and carry no voice: a stretch of A where nobody sings, or, with
    both stems, A's band with its voice taken out."""
    meter = fa["meter"]
    va = fa["vocal"]
    starts = np.where((fa["phrase"] >= PHRASE_4BAR) & (fa["pos"] == 0))[0]
    cands = []
    for nb in LOOP_BARS:
        n = nb * meter
        A = starts[(starts >= 2 * meter) & (starts + n + 4 < fa["n"])]
        if len(A) == 0:
            continue
        seam_full, seam_band = _seams(fa, n)
        sung = _gather(va, A, n).mean(1) >= LONG_BLEND_VOCAL
        seam = np.where(sung, seam_band[A], seam_full[A])
        dur_loop = fa["cum"][A + n] - fa["cum"][A]
        keep = (seam <= LOOP_SEAM_MAX) & _steady(fa, dur_loop / n) & _even(fa, A, n) & (stems | ~sung)
        A, seam, sung, dur_loop = A[keep], seam[keep], sung[keep], dur_loop[keep]
        if len(A) == 0:
            continue
        ha = _gather(fa["chroma"].mean(1), A, n).mean(1)
        ha /= np.linalg.norm(ha, axis=1, keepdims=True) + 1e-9
        ta = ((_gather(fa["mfcc"].mean(1), A, n).mean(1)) - mu) / sd
        loop_harm = _gather(harm_a, A, n).mean(1) > 0.5
        # Taking A's voice out in the middle of a sung line gives it away.
        cut = np.where(sung, va[A - 1] * va[A], 0.0)
        pre_l = np.array([fa["loud"][max(0, a - 2 * meter):a].mean() for a in A]) + fa["gain_db"]
        for L in (8 * meter, 16 * meter):
            if L // n > LOOP_MAX_REPEATS:
                continue
            B = _region_starts(fb, L, min(fb["n"], fb["lbp"]), True)
            if len(B) == 0:
                continue
            dur_b = fb["cum"][B + L] - fb["cum"][B]
            rate = (dur_b / L)[None, :] / (dur_loop / n)[:, None]
            lr = np.abs(np.log(rate))
            ok = (lr <= math.log(1 + BLEND_BRIDGE_MAX)) & _steady(fb, dur_b / L)[None, :] & _even(fb, B, L)[None, :]
            both_harm = loop_harm[:, None] & (_gather(harm_b, B, L).mean(1) > 0.5)[None, :]
            ok &= ~(both_harm & (_detune(rate, fa["tuning"], fb["tuning"]) > DETUNE_MAX_CENTS))
            if not ok.any():
                continue
            hb = _gather(fb["chroma"].mean(1), B, L).mean(1)
            hb /= np.linalg.norm(hb, axis=1, keepdims=True) + 1e-9
            semis, d_h, kp = _best_shift(fa, fb, ha, hb)
            ok &= ~(both_harm & (kp >= 0.35))
            if not ok.any():
                continue
            tile = np.arange(L) % n  # the loop beat under each beat of the blend
            ra = fa["rhythm"][A[:, None] + tile[None, :]].reshape(len(A), -1)
            rb = _gather(fb["rhythm"], B, L).reshape(len(B), -1)
            d_r = 1.0 - ra @ rb.T / L
            tb = ((_gather(fb["mfcc"].mean(1), B, L).mean(1)) - mu) / sd
            d_t = np.sqrt(((ta[:, None, :] - tb[None, :, :]) ** 2).sum(2)) / np.sqrt(ta.shape[1])
            post_l = np.array([fb["loud"][b + L:min(fb["n"], b + L + 2 * meter)].mean() if b + L < fb["n"]
                               else fb["loud"][-1] for b in B]) + fb["gain_db"]
            pen_e = 0.04 * np.maximum(0.0, np.abs(pre_l[:, None] - post_l[None, :]) - 2.0)
            bonus = FIT_WEIGHT * (fa["exit_fit"][A + n][:, None] + fb["entry_fit"][B][None, :])
            jolt = np.abs(fa["intensity"][A][:, None] - fb["intensity"][np.minimum(B + L, fb["n"] - 1)][None, :])
            pen_t = _tempo_penalty(lr, BLEND_MAX_RATE, 0.25, BLEND_BRIDGE_MAX)
            base = 0.25 if L == 8 * meter else 0.22
            S = (base + LOOP_COST + kp + 0.6 * d_h + 0.4 * d_r + 0.2 * d_t + pen_e + pen_t - bonus + 0.4 * jolt
                 + 0.35 * md + np.maximum(0.0, seam - 0.15)[:, None] + 0.5 * cut[:, None] + SHIFT_COST * np.abs(semis))
            S = np.where(ok, S, np.inf)
            for f in np.argsort(S, axis=None)[:100]:
                ai, bi = divmod(int(f), len(B))
                s = float(S[ai, bi])
                if not np.isfinite(s) or s > TAU_BLEND:
                    break
                cands.append({"a": int(A[ai]), "b": int(B[bi]), "len": L, "m": 1, "R": float(rate[ai, bi]),
                              "semis": int(semis[ai, bi]), "score": s, "clash": 0.0, "style": "loop",
                              "stems": bool(sung[ai]), "loop": n})
    return cands


FADE_KEEP = 2.2      # fades stored as regular exits up to this score (the smooth way across moods)
FADES_PER_PAIR = 3   # spread over the song, so an exit is never far away


def best_fade(fa: dict, fb: dict, to: str, kpen: float) -> dict | None:
    found = fades(fa, fb, to, kpen, 1, keep=np.inf)
    return found[0] if found else None


def fades(fa: dict, fb: dict, to: str, kpen: float, limit: int = FADES_PER_PAIR, keep: float = FADE_KEEP) -> list[dict]:
    """Crossfade exits from A to B at different places in A (at least 8 bars
    apart), each with the best entry point in B."""
    meter = fa["meter"]
    # The further apart the moods, the longer and gentler the crossfade: two
    # songs of one style swap in 5 s, a dance track into a ballad takes 11 s.
    seconds = float(np.clip(FADE_SECONDS + 1 + 5 * mood_distance(fa, fb), 5.0, 11.0))
    fade_beats_a = max(2, int(round(seconds / float(np.median(fa["dur"])))))
    fade_beats_b = max(2, int(round(seconds / float(np.median(fb["dur"])))))
    A = np.where(fa["phrase"] >= PHRASE_4BAR)[0]
    A = A[(A >= 2 * meter) & (A + fade_beats_a <= fa["n"])]
    B = _region_starts(fb, fade_beats_b, min(fb["n"], fb["lbp"]), True)
    if len(A) == 0 or len(B) == 0:
        return []
    da = _gather(sim.drum_presence(fa), A, fade_beats_a).mean(1)
    db = _gather(sim.drum_presence(fb), B, fade_beats_b).mean(1)
    # Two drum kits at different tempos flam within a few beats: a fade only
    # where at most one side has drums (an echo or a wash covers the rest).
    drums_clash = (da > DRUMS_CLASH)[:, None] & (db > DRUMS_CLASH)[None, :]
    L = min(fade_beats_a, fade_beats_b)
    k = np.arange(L)
    w = np.cos(0.5 * np.pi * k / L) * np.sin(0.5 * np.pi * k / L)
    clash = (_gather(fa["vocal"], A, L) * w) @ _gather(fb["vocal"], B, L).T / w.sum()
    pre = np.array([fa["loud"][max(0, a - 2 * meter):a].mean() for a in A]) + fa["gain_db"]
    post = np.array([fb["loud"][b:b + 2 * meter].mean() for b in B]) + fb["gain_db"]
    pen_e = 0.04 * np.maximum(0.0, np.abs(pre[:, None] - post[None, :]) - 3.0)
    # Where it leaves and enters by the songs' structure (B's intro, a drop, a
    # chorus, wherever in the song it is), a little better in a valley of A (an
    # outro, a breakdown). A long filtered crossfade is the way across a change
    # of mood and energy, so a change of intensity costs it little.
    fit = FIT_WEIGHT * (fa["exit_fit"][A][:, None] + fb["entry_fit"][B][None, :])
    ia, ib = fa["intensity"][A], fb["intensity"][B]
    jolt = np.abs(ia[:, None] - ib[None, :])
    S = (0.55 + 0.5 * da[:, None] + 0.5 * db[None, :] + 1.0 * clash + 0.7 * kpen + pen_e - fit
         + 0.2 * jolt + 0.1 * ia[:, None])
    S = np.where(drums_clash, np.inf, S)
    best_b = S.argmin(1)
    cands = [{"a": int(A[ai]), "b": int(B[best_b[ai]]), "score": float(S[ai, best_b[ai]])} for ai in range(len(A))
             if np.isfinite(S[ai, best_b[ai]])]
    out = []
    for c in _nms([c for c in cands if c["score"] <= keep], "a", 8 * meter, limit):
        out.append({"type": "fade", "to": to, "i": c["a"] - 1, "j": c["b"], "seconds": round(seconds, 1),
                    "rate": 1.0, "shift_ms": 0.0, "score": round(c["score"], 4), "q": _quality(c["score"], FADE_RANGE)})
    return out


FX_RANGE = (0.55, 1.20)
FX_KEEP = 1.2
FX_PER_PAIR = 2
# Beats of A the effect occupies before the new song comes in: an echo is fed
# by the last beat only and A is cut on the downbeat; a wash swells over two;
# a roll winds up over two bars (1-beat, then 1/2, 1/4, 1/8 repeats); a
# build-up rises over four bars; a spinback takes two beats; a gate and a
# flanger sweep two bars; a gap is one beat of silence.
FX_BEATS = {"echo": 1, "brake": 1, "roll": 8, "wash": 2, "riser": 16, "spin": 2, "gate": 8, "flanger": 8, "gap": 1}
# The newer accents are kept once per pair: they are the spice of a set.
FX_PER_KIND = {"riser": 1, "spin": 1, "gate": 1, "flanger": 1, "gap": 1}


def effects(fa: dict, fb: dict, to: str, kpen: float, keep: float = FX_KEEP, kinds=None,
            per_pair: int = FX_PER_PAIR) -> list[dict]:
    """Hand-overs that need neither a shared tempo nor alike music, only a good
    place to leave (the end of a phrase, not mid-line) and a good place to
    arrive (a phrase or section start, ideally at full energy):
      echo   A's last beat feeds a band-limited feedback delay that rings on
             under B; A is cut on the downbeat;
      wash   A swells into a long reverb and is cut on the downbeat: the way
             across a big change of mood or tempo;
      brake  A slows to a stop like a turntable, B hits on the next downbeat;
      roll   A's last two bars repeat in ever shorter slices under a rising
             filter, then B drops in;
      riser  a build-up: A thins out over four bars under a rising noise
             sweep, B lands on the one with an impact (into a strong entry);
      spin   a spinback: A's platter is thrown backwards, B drops in;
      gate   A chopped in eighths, then sixteenths, under a rising high-pass;
      flanger  A swept through a jet flanger as it leaves, B comes in under it;
      gap    one beat of silence, then B on the one (out of a loud A into a
             strong B: a dancefloor drop-out)."""
    ma, mb = fa["meter"], fb["meter"]
    X = np.where(fa["phrase"] >= PHRASE_4BAR)[0]  # A's natural next beat; leave after X - 1
    X = X[(X >= 4 * ma) & (X < fa["n"])]
    J = _region_starts(fb, 2 * mb, min(fb["n"], fb["lbp"]), True)
    if len(X) == 0 or len(J) == 0:
        return []
    va, vb = fa["vocal"], fb["vocal"]
    cut_line = (va[X - 1] * va[X])[:, None]          # leaving in the middle of a sung line
    line_end = (va[X - 1] * (1 - va[X]))[:, None]    # the last word of a line: what an echo loves
    b_voc = vb[J][None, :]
    dpa = sim.drum_presence(fa)
    drums_end = (0.5 * (dpa[X - 1] + dpa[X - 2]))[:, None]
    lb = fb["loud"]
    hit_b = np.clip((np.array([lb[j:j + mb].mean() for j in J]) - lb.max() + 12) / 12, 0, 1)[None, :]
    pre_l = np.array([fa["loud"][x - 2 * ma:x].mean() for x in X]) + fa["gain_db"]
    post_l = np.array([lb[j:j + 2 * mb].mean() for j in J]) + fb["gain_db"]
    pen_lvl = 0.03 * np.maximum(0.0, np.abs(pre_l[:, None] - post_l[None, :]) - 3.0)
    # A loud end of A (against its loudest), and B entering on a drop or a chorus.
    loud_a = np.clip((pre_l - fa["gain_db"] - fa["loud"].max() + 12) / 12, 0, 1)[:, None]
    peak_b = (np.isin(fb["role"][J], (ROLE_DROP, ROLE_CHORUS)) & (fb["phrase"][J] == PHRASE_SECTION))[None, :]
    # Leaving as A's section ends, entering B at its intro, a drop, a chorus.
    sec_a = FIT_WEIGHT * fa["exit_fit"][X][:, None]
    sec_b = FIT_WEIGHT * fb["entry_fit"][J][None, :]
    jolt = np.abs(fa["intensity"][X - 1][:, None] - fb["intensity"][J][None, :])
    md = mood_distance(fa, fb)
    # How far apart the tempos are (double and half time count as alike).
    lt = abs(math.log2(fb["bpm"] / fa["bpm"]))
    tempo_far = float(abs(lt - round(lt)) > math.log2(1.06))
    # An echo, a wash or a flanger leaves A whatever comes next, and B comes in
    # under the tail filtered and opening up: a change of energy either way
    # costs them little.
    scores = {
        "echo": (0.55 + 0.8 * cut_line - 0.15 * line_end + 0.25 * b_voc + 0.5 * kpen + pen_lvl - sec_a - sec_b
                 + 0.25 * jolt + 0.3 * md),
        # The long tail blurs whatever differs: the wider the gap between the
        # songs, the better a wash fits (and the less an echo does).
        "wash": (0.62 + 0.8 * cut_line - 0.1 * line_end + 0.2 * b_voc + 0.4 * kpen + pen_lvl - sec_a - sec_b
                 + 0.15 * jolt - 0.2 * min(md, 1.5) - 0.1 * tempo_far),
        "brake": 0.6 + 0.8 * cut_line + 0.2 * b_voc + 0.6 * pen_lvl - 0.12 * hit_b - sec_a - sec_b + 0.6 * md,
        "roll": (0.6 + 0.6 * cut_line + 0.45 * (1 - drums_end) + 0.2 * b_voc - 0.15 * hit_b - sec_a - sec_b
                 + 0.6 * md),
        # Only into an entry with the energy to answer the build: a drop or
        # a chorus at full level.
        "riser": (0.58 + 0.8 * cut_line + 0.2 * b_voc + 0.35 * (1 - hit_b) - 0.1 * peak_b + pen_lvl - sec_a - sec_b
                  + 0.3 * md),
        "spin": 0.62 + 0.8 * cut_line + 0.2 * b_voc + 0.6 * pen_lvl - 0.15 * hit_b - sec_a - sec_b + 0.5 * md,
        # A gate chops a groove; on held chords it only stutters.
        "gate": (0.6 + 0.6 * cut_line + 0.45 * (1 - drums_end) + 0.2 * b_voc - 0.12 * hit_b - sec_a - sec_b
                 + 0.5 * md),
        "flanger": (0.6 + 0.8 * cut_line - 0.1 * line_end + 0.2 * b_voc + 0.4 * kpen + pen_lvl - sec_a - sec_b
                    + 0.25 * jolt + 0.15 * md),
        # Silence only means something after a loud A, before a strong B.
        "gap": (0.6 + 0.9 * cut_line + 0.2 * b_voc + 0.3 * (1 - hit_b) + 0.2 * (1 - loud_a) - 0.1 * peak_b + pen_lvl
                - sec_a - sec_b + 0.4 * md),
    }
    out = []
    for kind, S in scores.items():
        if kinds and kind not in kinds:
            continue
        best_b = S.argmin(1)
        cands = [{"x": int(X[ai]), "j": int(J[best_b[ai]]), "score": float(S[ai, best_b[ai]])}
                 for ai in range(len(X)) if S[ai, best_b[ai]] <= keep and X[ai] >= FX_BEATS[kind] + 1]
        for c in _nms(cands, "x", 8 * ma, min(per_pair, FX_PER_KIND.get(kind, per_pair))):
            # Effects are the spice: their quality tops out below a clean
            # beat-matched move, so the planner reaches for them when the music
            # offers nothing better nearby, not every time.
            q = min(0.7, 0.2 + 0.5 * (FX_RANGE[1] - c["score"]) / (FX_RANGE[1] - FX_RANGE[0]))
            out.append({"type": kind, "to": to, "i": c["x"] - 1, "j": c["j"], "beats": FX_BEATS[kind],
                        "rate": 1.0, "shift_ms": 0.0, "score": round(c["score"], 4), "q": round(max(0.05, q), 3)})
    return out


DROP_RANGE = (0.45, 1.0)


def drops(fa: dict, fb: dict, to: str) -> list[dict]:
    """A hard cut on the "one" of a phrase into a strong section of B, with the
    tempo bent so the groove carries straight on. Unlike `cuts` it does not ask
    the two moments to sound alike, only to be phrase-aligned, rhythmically
    compatible and harmonically close once B is transposed by its rate."""
    if fa["meter"] != fb["meter"]:
        return []
    meter = fa["meter"]
    X = np.where(fa["phrase"] >= PHRASE_4BAR)[0]
    X = X[(X >= 4 * meter) & (X < fa["n"])]
    J = np.where(fb["phrase"] >= PHRASE_4BAR)[0]
    J = J[(J >= 1) & (J <= fb["lbp"] - 2 * meter)]
    if len(X) == 0 or len(J) == 0:
        return []
    R = fb["local_ibi"][J][None, :] / fa["local_ibi"][X][:, None]
    lr = np.abs(np.log(R))
    ok = (lr <= math.log(1 + BRIDGE_MAX)) & _steady(fa, fa["local_ibi"][X])[:, None] & _steady(fb, fb["local_ibi"][J])[None, :]
    va, vb = fa["vocal"], fb["vocal"]
    pre_m, rb_m = _bridge(R, CUT_MAX_RATE)
    ok &= ~_bent_voice(pre_m, rb_m, _behind(va, 16)[X][:, None], _ahead(vb, 8)[J][None, :])
    if not ok.any():
        return []
    # Leaving mid-line, in absolute terms and relative to this song's own
    # phrase ends: a song that is sung end to end still has its least-sung
    # moments, and those are where it should be left.
    line = va[X - 1] * va[X]
    rel = (line - line.min()) / (line.max() - line.min() + 1e-9)
    cut_line = (0.45 * line + 0.45 * rel)[:, None]
    # The groove must survive the cut: compare the beat patterns around it.
    ra = fa["rhythm"][X - 1] + fa["rhythm"][X - 2]
    rb = fb["rhythm"][J] + fb["rhythm"][J + 1]
    ra /= np.linalg.norm(ra, axis=1, keepdims=True) + 1e-9
    rb /= np.linalg.norm(rb, axis=1, keepdims=True) + 1e-9
    d_r = 1.0 - ra @ rb.T
    dpa, dpb = sim.drum_presence(fa), sim.drum_presence(fb)
    d_drums = np.abs(dpa[X - 1][:, None] - dpb[J][None, :])
    lb = fb["loud"]
    energy_b = np.clip((lb[J] - lb.max() + 10) / 10, 0, 1)[None, :]
    semis = _semis(R)
    kp = np.full(R.shape, 0.7)
    for s in np.unique(semis[ok]):
        kp[semis == s] = _key_penalty(fa, fb, int(s))[0]
    # A drop lands best on B's own drop or chorus, as A's section ends.
    sec_a = FIT_WEIGHT * fa["exit_fit"][X][:, None]
    sec_b = FIT_WEIGHT * fb["entry_fit"][J][None, :]
    # Nothing overlaps at a drop, so a key change reads as a modulation (a
    # semitone up is a pop classic) rather than a clash: key weighs less here.
    # A drop is how a set lifts: from a calmer A into B's drop or chorus is
    # what it is for; dropping from full energy into a quiet stretch is not.
    rise, fall = _rise_fall(fa["intensity"][X - 1], fb["intensity"][J])
    S = (0.45 + 0.35 * kp + cut_line + 0.5 * d_r + 0.4 * d_drums - 0.1 * energy_b
         + _tempo_penalty(lr, CUT_MAX_RATE, 0.12) - sec_a - sec_b
         + 0.3 * rise + 1.0 * fall + 0.8 * mood_distance(fa, fb))
    S = np.where(ok, S, np.inf)
    best_b = S.argmin(1)
    cands = [{"x": int(X[ai]), "j": int(J[best_b[ai]]), "score": float(S[ai, best_b[ai]]),
              "R": float(R[ai, best_b[ai]]), "semis": int(semis[ai, best_b[ai]])}
             for ai in range(len(X)) if S[ai, best_b[ai]] <= DROP_RANGE[1]]
    out = []
    ibi = float(np.median(fa["dur"]))
    for c in _nms(cands, "x", 8 * meter, 2):
        pre, rb_ = (float(v) for v in _bridge(np.array(c["R"]), CUT_MAX_RATE))
        shift, _ = sim.align_offset(fa["onset_env"], fa["beats"][c["x"]], fb["onset_env"], fb["beats"][c["j"]],
                                    fa["env_rate"], ibi, rate_b=c["R"])
        q = min(0.85, _quality(c["score"], DROP_RANGE))
        out.append({"type": "drop", "to": to, "i": c["x"] - 1, "j": c["j"], "rate": round(rb_, 5),
                    "pre": round(pre, 5), "semis": c["semis"], "m": 1, "keylock": _keylocked(rb_),
                    "shift_ms": round(shift * 1000, 2),
                    "score": round(c["score"], 4), "q": round(q, 3), "xf_ms": 14.0})
    return out


def pair(a: str, b: str) -> dict:
    fa, fb = load_features(a), load_features(b)
    kpen, kd = _key_penalty(fa, fb)
    # Stem hand-overs only between two songs whose vocal stems are on the server.
    stems = library.has_vocals(a) and library.has_vocals(b)
    items = (cuts(fa, fb, b) + drops(fa, fb, b) + blends(fa, fb, b, stems) + fades(fa, fb, b, kpen)
             + effects(fa, fb, b, kpen))
    # The way across kept for when nothing better links two songs: the best
    # fade where at most one side has drums, else the best echo or wash.
    fade = best_fade(fa, fb, b, kpen)
    if fade is None:
        spare = effects(fa, fb, b, kpen, keep=np.inf, kinds=("echo", "wash"), per_pair=1)
        fade = min(spare, key=lambda t: t["score"]) if spare else None
    # The planner steers the chain toward songs of a near mood, so a session
    # drifts from style to style instead of lurching; and, shaping a set, by
    # the energy each hand-over leads into.
    md = round(mood_distance(fa, fb), 3)
    for t in items + ([fade] if fade else []):
        t["mood"] = md
        t["oe"] = _exit_energy(fa, t)
        t["ie"] = _entry_energy(fb, t)
    return {"items": items, "fade": fade, "tempo_ratio": round(fb["bpm"] / fa["bpm"], 4), "key_distance": kd}


def _song_energy(f: dict, rel: float) -> float:
    """A section's energy across songs: the song's own (drums and tempo) times
    how intense the section is within it."""
    return round(float(f["mood"]["energy"]) * (0.4 + 0.6 * rel), 3)


def _exit_energy(fa: dict, t: dict) -> float:
    """0..1: A's energy over the eight bars before it starts to give way."""
    meter = fa["meter"]
    if t["type"] == "blend":
        end = t["a"]
    else:
        end = t["i"] + 1 - t.get("beats", 1) if t["type"] in FX_BEATS else t["i"] + 1  # where the effect starts
    end = min(max(1, int(end)), fa["n"])
    return _song_energy(fa, float(fa["intensity"][max(0, end - 8 * meter):end].mean()))


def _entry_energy(fb: dict, t: dict) -> float:
    """0..1: how much energy B has where it plays on alone after a hand-over,
    across songs (its own energy: drums and tempo) and within it (the section's
    intensity over the next eight bars): a dance track's drop near 1, its
    breakdown about half that, a ballad's chorus low."""
    meter = fb["meter"]
    if t["type"] == "blend":
        start = t["b"] + int(round(t.get("m", 1) * t["len"]))
    else:
        start = t["j"] if t.get("j") is not None else 0
    start = min(max(0, int(start)), fb["n"] - 1)
    return _song_energy(fb, float(fb["intensity"][start:start + 8 * meter].mean()))



# ----------------------------------------------------------------- the store
#
# Transitions are stored per ordered pair of songs (data/pairs/<a>/<b>.json):
# what pair(a, b) found, which depends on those two songs only. A listener's
# library is any set of songs; its transition lists are assembled from the
# pairs (assemble), so a pair computed for one listener serves every other.

_pair_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_pair_cache_lock = threading.Lock()


# Pairs from this version on still play (their kinds are a subset of today's)
# while the current search recomputes them, so a library keeps its exits.
OLDEST_PLAYABLE = 7


def has_pair(a: str, b: str) -> bool:
    """Whether the pair is stored by the current search (else it is computed)."""
    data = _load_pair(a, b)
    return data is not None and data.get("version") == TRANSITIONS_VERSION


def _load_pair(a: str, b: str) -> dict | None:
    path = library.pair_path(a, b)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _pair_cache_lock:
        hit = _pair_cache.get((a, b))
        if hit and hit[0] == mtime:
            return hit[1]
    data = library.read_json(path)
    if not data or not OLDEST_PLAYABLE <= data.get("version", 0) <= TRANSITIONS_VERSION:
        return None
    with _pair_cache_lock:
        if len(_pair_cache) > 50_000:
            _pair_cache.clear()
        _pair_cache[(a, b)] = (mtime, data)
    return data


def compute_pair(a: str, b: str) -> None:
    r = pair(a, b)
    path = library.pair_path(a, b)
    on_disk = library.read_json(path) or {}
    if on_disk.get("version", 0) > TRANSITIONS_VERSION:
        # Written by a newer version of the code: a stale server must not
        # downgrade it.
        return
    items = sorted(r["items"], key=lambda t: -t["q"])
    library.write_json(path, {"version": TRANSITIONS_VERSION, "from": a, "to": b, "items": items, "fade": r["fade"]})


def link(tid: str, others: list[str]) -> int:
    """Computes the missing pairs between tid and each of `others`, both ways.
    Returns how many were computed."""
    n = 0
    for o in others:
        if o == tid:
            continue
        for a, b in ((tid, o), (o, tid)):
            if not has_pair(a, b):
                try:
                    compute_pair(a, b)
                    n += 1
                except FileNotFoundError:
                    pass  # one of the songs left the store meanwhile
    return n


def missing_pairs(ids: list[str]) -> list[tuple[str, str]]:
    return [(a, b) for a in ids for b in ids if a != b and not has_pair(a, b)]


def _with_fallback(items: list[dict], fades: dict[str, dict]) -> list[dict]:
    """Keep each pair's fade only when the pair has nothing better, and make sure the
    track keeps at least MIN_LINKS distinct destinations."""
    have = {t["to"] for t in items}
    out = list(items)
    spare = sorted((f for to, f in fades.items() if to not in have and f), key=lambda f: f["score"])
    for f in spare:
        if len({t["to"] for t in out}) >= MIN_LINKS and f["score"] > 1.0:
            break
        out.append(f)
    return out


def assemble(ids: list[str]) -> dict[str, list[dict]]:
    """The transition list of every song in a library, from the stored pairs
    (missing pairs are skipped: they appear once computed)."""
    ids = list(dict.fromkeys(ids))
    out: dict[str, list[dict]] = {}
    fades_of: dict[str, dict[str, dict]] = {}
    for a in ids:
        items, fades = [], {}
        for b in ids:
            if a == b:
                continue
            p = _load_pair(a, b)
            if p:
                items += p["items"]
                fades[b] = p["fade"]
        fades_of[a] = fades
        out[a] = _with_fallback(items, fades)
    # A song that no transition leads into is never heard in a chain: give
    # every song at least MIN_IN ways in, using the best fades if needed.
    inc = {d: set() for d in ids}
    for a, items in out.items():
        for t in items:
            if t["to"] in inc:
                inc[t["to"]].add(a)
    for d in ids:
        missing = MIN_IN - len(inc[d])
        if missing <= 0:
            continue
        cands = [(f["score"], o, f) for o in ids if o != d and o not in inc[d] and (f := fades_of[o].get(d))]
        for _, o, f in sorted(cands, key=lambda c: c[0])[:missing]:
            out[o].append(f)
            inc[d].add(o)
    for items in out.values():
        items.sort(key=lambda t: (t["to"], -t["q"]))
    return out


# The ways on from a song the chain looks at one step ahead: those that keep
# the beat (a crossfade or an effect is nearly always there, so they tell
# songs apart no better than noise), best first.
ONWARD_KINDS = frozenset({"cut", "drop", "blend"})
ONWARD_KEEP = 8


def onward(assembled: dict[str, list[dict]]) -> dict[str, list[list]]:
    """Per song of a library, its destinations with the quality of the best
    beat-matched transition to each, best first: what the chain finds one song
    further on, so it does not walk into a song it can only fade out of."""
    out = {}
    for a, items in assembled.items():
        best: dict[str, float] = {}
        for t in items:
            if t["type"] in ONWARD_KINDS and t["q"] > best.get(t["to"], -1.0):
                best[t["to"]] = t["q"]
        out[a] = [[b, round(q, 3)] for b, q in sorted(best.items(), key=lambda kv: -kv[1])[:ONWARD_KEEP]]
    return out


def export(ids: list[str]) -> None:
    """Writes the assembled lists as data/transitions/<id>.json for the test tools."""
    lists = assemble(ids)
    ahead = onward(lists)
    for tid, items in lists.items():
        on = {d: ahead.get(d, []) for d in {t["to"] for t in items}}
        library.write_json(library.transitions_path(tid),
                           {"version": TRANSITIONS_VERSION, "from": tid, "items": items, "onward": on})
    write_marker()


def relink_all(on_progress=lambda i, n: None) -> None:
    """Recomputes every pair of the whole store from scratch."""
    import shutil
    ids = library.ready_ids()
    shutil.rmtree(library.PAIRS_DIR, ignore_errors=True)
    library.PAIRS_DIR.mkdir(parents=True, exist_ok=True)
    with _pair_cache_lock:
        _pair_cache.clear()
    done: list[str] = []
    for i, tid in enumerate(ids):
        link(tid, done)
        done.append(tid)
        on_progress(i + 1, len(ids))
    export(ids)


def marker_path():
    return library.TRANSITIONS_DIR / "_version.json"


def write_marker() -> None:
    """Records which version of the transition search produced the files."""
    if (library.read_json(marker_path()) or {}).get("version", 0) > TRANSITIONS_VERSION:
        return
    library.write_json(marker_path(), {"version": TRANSITIONS_VERSION})
