"""Song-level mood, used to steer the chain (similar moods next to each other)
and to colour the interface (each song gets the colour of its mood)."""
import math

import numpy as np

from . import similarity as sim


def song_mood(f: dict, bpm: float, key_mode: int) -> dict:
    """Drive (drums), tempo feel (folded into 80-160 BPM), brightness (MFCC 1,
    the spectral tilt), how sung it is, major or minor, dynamic range; plus the
    two axes of Thayer's mood plane derived from them, energy and valence, 0..1."""
    fold = math.log2(max(1.0, bpm))
    while fold < math.log2(80):
        fold += 1
    while fold >= math.log2(160):
        fold -= 1
    drums = float(np.median(sim.drum_presence(f)))
    bright = float(f["mfcc"][:, :, 0].mean())
    major = 1.0 if key_mode == 0 else 0.0
    tempo01 = (2 ** fold - 80.0) / 80.0
    bright01 = float(np.clip((bright - 50.0) / 65.0, 0, 1))
    return {
        "drums": drums,
        "fold": fold,
        "bright": bright,
        "vocal": float(f["vocal"].mean()),
        "major": major,
        "dyn": float(np.percentile(f["loud"], 90) - np.percentile(f["loud"], 10)),
        "energy": round(float(np.clip(0.6 * drums + 0.4 * tempo01, 0, 1)), 3),
        "valence": round(float(np.clip(0.55 * major + 0.45 * bright01, 0, 1)), 3),
    }
