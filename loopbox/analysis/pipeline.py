"""Full analysis of one track: audio.flac -> analysis.json + features.npz, and
vocals.mp3 (the vocal stem, stereo, aligned with the track's own MP3) for a
sung track, which lets the player hand over one layer at a time."""
import os
import time
from typing import Callable

import numpy as np
import soundfile as sf

from .. import audio, library
from ..config import ANALYSIS_SR, ANALYSIS_VERSION, SAMPLE_RATE, TARGET_LUFS
from . import beats as beatmod
from . import features as featmod
from . import jumps as jumpmod
from . import models, similarity, structure
from .beats import HR_HOP
from .mood import song_mood

# Below this mean vocal presence a track is treated as instrumental: no stem kept.
STEM_MIN_VOCAL = 0.05


def save_features(tid: str, **arrays) -> None:
    """features.npz written whole or not at all: the linking processes read it
    while a track is being analysed again in the background."""
    path = library.features_path(tid)
    tmp = path.with_suffix(".tmp.npz")
    with open(tmp, "wb") as fh:
        np.savez_compressed(fh, **arrays)
    os.replace(tmp, path)


def _save_vocals(tid: str, vocals: np.ndarray | None) -> None:
    """The vocal stem as an MP3 (about 5 MB for a 4-minute song), encoded like
    the track itself so the two stay aligned."""
    d = library.track_dir(tid)
    for old in d.glob("vocals.*"):
        old.unlink(missing_ok=True)
    if vocals is None:
        return
    tmp = d / "vocals.tmp.flac"
    sf.write(str(tmp), np.clip(vocals, -1.0, 1.0), SAMPLE_RATE, format="FLAC", subtype="PCM_16")
    try:
        for fmt in library.FORMATS:
            audio.encode(tmp, library.transport_path(tid, "vocals", fmt), fmt, audio.BITRATE["vocals"][fmt])
    finally:
        tmp.unlink(missing_ok=True)


def analyze(tid: str, on_stage: Callable[[str, float], None] = lambda s, p: None) -> dict:
    t0 = time.time()
    path = library.audio_path(tid)
    meta = library.read_meta(tid) or {}

    on_stage("loading", 0.02)
    y = audio.load(path)
    duration = len(y) / SAMPLE_RATE
    lufs = audio.integrated_loudness(path)
    gain_db = float(np.clip(TARGET_LUFS - lufs, -12.0, 6.0))

    on_stage("beats", 0.1)
    raw_beats, downbeats = beatmod.track(y)
    if len(raw_beats) < 32:
        raise RuntimeError("too few beats detected: not a rhythmic track?")
    y22 = featmod.mono22(y)
    env = featmod.onset_hr(y22)
    env_rate = ANALYSIS_SR / HR_HOP
    beats = beatmod.refine(raw_beats, env, env_rate)
    pos, meter = beatmod.bar_positions(beats, downbeats)

    on_stage("stems", 0.35)
    stems, names, stereo = models.separate(y, keep_stereo=("vocals",))
    order = [names.index(s) for s in featmod.STEMS]
    stems = stems[order]

    on_stage("features", 0.65)
    feat = featmod.compute(y, y22, beats, stems)
    del stems
    sung = float(feat["vocal"].mean()) >= STEM_MIN_VOCAL
    _save_vocals(tid, stereo["vocals"] if sung else None)
    del stereo
    scales = similarity.own_scales(feat)

    on_stage("structure", 0.75)
    bpm, local_ibi = structure.tempo(beats)
    key = structure.estimate_key(feat["key_profile"])
    sec = structure.sections(feat, pos, meter)
    phrase = structure.phrase_grid(pos, sec, meter)
    rl = structure.roles(feat, pos, sec)

    on_stage("jumps", 0.85)
    jr = jumpmod.find(feat, pos, meter, scales, beats, env, env_rate, phrase)
    md = song_mood(feat, bpm, 0 if key["mode"] == "major" else 1)

    analysis = {
        "version": ANALYSIS_VERSION,
        "id": tid,
        "title": meta.get("title", tid),
        "duration": round(duration, 4),
        "sr": SAMPLE_RATE,
        "bpm": round(bpm, 2),
        "meter": meter,
        "key": key["name"],
        "camelot": key["camelot"],
        "lufs": round(lufs, 2),
        "gain_db": round(gain_db, 2),
        "beats": [round(float(b), 5) for b in beats],
        "pos": pos.tolist(),
        "phrase": phrase.tolist(),
        "loud": [round(float(v) + gain_db, 1) for v in feat["loud"]],
        "vocal": [round(float(v), 2) for v in feat["vocal"]],
        "color": featmod.timbre_colors(feat["mfcc"]),
        "sections": sec.tolist(),
        "roles": rl["sections"],
        "tuning": round(float(feat["tuning"]), 1),
        "key_conf": round(key["confidence"], 3),
        "stems": sung,
        "jumps": jr["jumps"],
        "lbp": jr["lbp"],
        "fallback": jr["fallback"],
        "mood": {"energy": md["energy"], "valence": md["valence"]},
    }
    save_features(
        tid,
        **feat,
        beats=beats, pos=pos, phrase=phrase, local_ibi=local_ibi, onset_env=env,
        mfcc_mu=scales[0], mfcc_sd=scales[1], scales=scales[2],
        meter=meter, bpm=bpm, gain_db=gain_db, lbp=jr["lbp"],
        key_tonic=key["tonic"], key_mode=0 if key["mode"] == "major" else 1,
        key_camelot=key["camelot_num"], key_conf=key["confidence"], env_rate=env_rate,
        role=rl["role"], bar_in_sec=rl["bar_in_sec"], bars_left=rl["bars_left"],
    )
    library.write_json(library.analysis_path(tid), analysis)
    stats = {
        "duration": round(duration, 1), "bpm": round(bpm, 1), "key": key["name"],
        "camelot": key["camelot"], "meter": meter, "beats": len(beats),
        "jumps": len(jr["jumps"]), "lbp": jr["lbp"], "fallback": jr["fallback"],
        "analysis_version": ANALYSIS_VERSION, "analysis_seconds": round(time.time() - t0, 1),
        "mood": analysis["mood"],
    }
    on_stage("analyzed", 0.95)
    return stats


def recompute_jumps(tid: str) -> dict:
    """Re-runs only the jump search from the stored features (seconds, no GPU)."""
    with np.load(library.features_path(tid)) as z:
        f = {k: z[k] for k in z.files}
    feat = {k: f[k] for k in ("chroma", "mfcc", "loud", "rhythm", "stems", "vocal", "dur", "vpitch", "vmfcc") if k in f}
    scales = similarity.own_scales(feat)
    f["mfcc_mu"], f["mfcc_sd"], f["scales"] = scales
    jr = jumpmod.find(feat, f["pos"], int(f["meter"]), scales, f["beats"], f["onset_env"], float(f["env_rate"]),
                      f["phrase"])
    analysis = library.read_json(library.analysis_path(tid))
    md = song_mood(feat, float(f["bpm"]), int(f["key_mode"]))
    analysis.update(jumps=jr["jumps"], lbp=jr["lbp"], fallback=jr["fallback"],
                    mood={"energy": md["energy"], "valence": md["valence"]})
    library.write_json(library.analysis_path(tid), analysis)
    f["lbp"] = np.array(jr["lbp"])
    save_features(tid, **f)
    library.update(tid, jumps=len(jr["jumps"]), lbp=jr["lbp"], fallback=jr["fallback"],
                   mood={"energy": md["energy"], "valence": md["valence"]})
    return jr
