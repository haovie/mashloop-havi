"""Where jumps land, musically: python tools/eval_voice.py [id ...]

For the jumps a listener gets at the default strictness (--keep=0.575):
  register  share that jump between passages sung more than 5 semitones apart
            (8-beat median vocal pitch, measured with pyin on the vocal stem,
            independently of the analyser's own yin pitch) - a male verse to a
            female one, typically
  mid-line  share that cut into a sung line on both sides of the junction
  phrase    share that keep the bar position inside the 4-bar phrase
  downbeat  share placed on the first beat of a bar
"""
import sys
import warnings
from pathlib import Path

import librosa
import numpy as np
import soxr

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loopbox import audio, library  # noqa: E402
from loopbox.analysis import models  # noqa: E402
from loopbox.analysis.jumps import bar_in_phrase  # noqa: E402

DEFAULT_KEEP = 0.575  # the player's default strictness: best 57.5 % of each song's jumps


def score_cut(scores: list[float], keep: float) -> float:
    """Same rule as Track.scoreCut in web/js/graph.js."""
    s = sorted(scores)
    if not s:
        return 0.2
    k = min(len(s) - 1, max(0, int(np.ceil(keep * len(s))) - 1))
    return max(0.2, s[k])
CACHE = library.LIBRARY_DIR.parent / "eval_cache"


def register(tid: str, beats: np.ndarray) -> np.ndarray:
    """8-beat median vocal pitch (MIDI) per beat, via pyin; cached per track."""
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{tid}_pyin.npy"
    if path.exists():
        per_beat = np.load(path)
        if len(per_beat) == len(beats):
            return _smooth(per_beat)
    y = audio.load(library.audio_path(tid))
    stems, names, _ = models.separate(y)
    voc = soxr.resample(stems[names.index("vocals")].astype(np.float32), 44100, 22050)
    f0, vflag, _ = librosa.pyin(voc, fmin=65, fmax=1000, sr=22050, frame_length=2048, hop_length=1024)
    times = librosa.times_like(f0, sr=22050, hop_length=1024)
    midi = librosa.hz_to_midi(f0)
    ends = np.append(beats[1:], beats[-1] + np.median(np.diff(beats)))
    per_beat = np.full(len(beats), np.nan)
    for b, (s, e) in enumerate(zip(beats, ends)):
        m = (times >= s) & (times < e) & vflag
        if m.sum() >= 2:
            per_beat[b] = np.median(midi[m])
    np.save(path, per_beat)
    return _smooth(per_beat)


def _smooth(per_beat: np.ndarray) -> np.ndarray:
    n = len(per_beat)
    out = np.full(n, np.nan)
    for b in range(n):
        w = per_beat[max(0, b - 4):b + 4]
        if np.any(~np.isnan(w)):
            out[b] = np.nanmedian(w)
    return out


def evaluate(tid: str) -> dict:
    a = library.read_json(library.analysis_path(tid))
    with np.load(library.features_path(tid)) as z:
        vocal, pos, phrase = z["vocal"], z["pos"], z["phrase"]
    beats = np.array(a["beats"])
    reg = register(tid, beats)
    bip = bar_in_phrase(pos, phrase)
    sing = np.maximum(vocal, np.r_[0.0, vocal[:-1]])
    rows = []
    limit = score_cut([jj[2] for jj in a["jumps"]], KEEP)
    for i, j, score, *rest in a["jumps"]:
        bridge = len(rest) >= 3 and rest[2]
        if score > limit and not bridge:
            continue
        x = i + 1
        if x >= len(beats):
            continue
        cross = None
        if not np.isnan(reg[x]) and not np.isnan(reg[j]) and min(sing[x], sing[j]) > 0.3:
            cross = abs(reg[x] - reg[j]) > 5
        rows.append({
            "cross": cross,
            "midline": min(sing[x], sing[j]) > 0.5,
            "phrase": bip[x] >= 0 and bip[x] == bip[j],
            "downbeat": pos[x] == 0,
        })
    sung = [r for r in rows if r["cross"] is not None]
    pct = lambda xs: f"{100 * np.mean(xs):3.0f}%" if len(xs) else "  - "  # noqa: E731
    out = {
        "n": len(rows),
        "register": pct([r["cross"] for r in sung]),
        "midline": pct([r["midline"] for r in rows]),
        "phrase": pct([r["phrase"] for r in rows]),
        "downbeat": pct([r["downbeat"] for r in rows]),
    }
    print(f"{a['title'][:42]:<42} jumps {out['n']:4d}  register-cross {out['register']}  mid-line {out['midline']}"
          f"  phrase-kept {out['phrase']}  on-downbeat {out['downbeat']}")
    return out


KEEP = DEFAULT_KEEP

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0].startswith("--keep="):
        KEEP = float(args.pop(0).split("=", 1)[1])
    print(f"strictness: best {KEEP:.0%} of each song's jumps")
    for tid in args or library.ready_ids():
        evaluate(tid)
