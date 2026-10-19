"""Print diagnostics for analysed tracks: python tools/inspect_track.py [id ...]"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loopbox import library  # noqa: E402
from loopbox.analysis import transitions  # noqa: E402


def describe(tid: str) -> None:
    a = library.read_json(library.analysis_path(tid))
    meta = library.read_meta(tid)
    f = transitions.load_features(tid)
    beats = np.array(a["beats"])
    ibi = np.diff(beats)
    jumps = np.array(a["jumps"]) if a["jumps"] else np.zeros((0, 4))
    print(f"== {tid}  {a['title'][:60]}")
    print(f"   {a['duration']:.0f}s  bpm={a['bpm']}  meter={a['meter']}  key={a['key']} ({a['camelot']})  "
          f"lufs={a['lufs']} gain={a['gain_db']}  analysis={meta.get('analysis_seconds')}s")
    print(f"   beats={len(beats)}  ibi median={np.median(ibi)*1000:.1f}ms  "
          f"jitter (|d ibi| median)={np.median(np.abs(np.diff(ibi)))*1000:.2f}ms  invalid pos={(np.array(a['pos']) < 0).sum()}")
    print(f"   sections at beats {a['sections']}")
    print(f"   vocal>0.5: {np.mean(np.array(a['vocal']) > 0.5):.0%} of beats")
    if len(jumps):
        d = jumps[:, 1] - (jumps[:, 0] + 1)
        print(f"   jumps={len(jumps)}  lbp={a['lbp']}/{len(beats)}  fallback={a['fallback']}  "
              f"back={np.sum(d < 0)} fwd={np.sum(d > 0)}")
        print(f"   score p10/50/90 = {np.percentile(jumps[:, 2], [10, 50, 90]).round(3)}  "
              f"distance beats p10/50/90 = {np.percentile(np.abs(d), [10, 50, 90]).astype(int)}")
        print(f"   align shift ms: nonzero={np.mean(jumps[:, 3] != 0):.0%}  "
              f"abs p50/90 = {np.percentile(np.abs(jumps[:, 3]), [50, 90]).round(1)}")
        srcs = np.unique(jumps[:, 0]).size
        print(f"   beats with a branch: {srcs} ({srcs / len(beats):.0%})")
    items = transitions.read(tid)
    if items:
        by = {}
        for t in items:
            by.setdefault(t["to"], []).append(t)
        print(f"   transitions out: {len(items)} to {len(by)} tracks")
        for to, ts in by.items():
            title = (library.read_meta(to) or {}).get("title", to)[:40]
            desc = ", ".join(f"{t['type']}(q={t['q']:.2f}, r={t['rate']:.3f})" for t in ts[:5])
            print(f"     -> {title:<40} {desc}")


if __name__ == "__main__":
    ids = sys.argv[1:] or library.ready_ids()
    for tid in ids:
        describe(tid)
