"""How well the library is linked: python tools/link_report.py

For every ordered pair of songs, the best stored transition kind; how many
destinations each song can reach by a beat-matched move (cut or blend); how
many of those need tempo bending or a double/half-time beat ratio."""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loopbox import library  # noqa: E402
from loopbox.analysis import transitions as tr  # noqa: E402

RANK = {"cut": 0, "blend": 1, "drop": 1, "echo": 2, "roll": 2, "brake": 2, "fade": 3}
KINDS = ("cut", "blend", "drop", "echo", "roll", "brake", "fade", "none")
BEAT_MATCHED = ("cut", "blend", "drop")


def main():
    ids = library.ready_ids()
    title = {i: (library.read_meta(i) or {}).get("title", i)[:34] for i in ids}
    pair_best = Counter()
    per_song = []
    bent = halfdouble = matched = 0
    for a in ids:
        items = [t for t in tr.read(a) if t["to"] in title]
        best = {}
        for t in items:
            if t["to"] not in best or RANK[t["type"]] < RANK[best[t["to"]]["type"]]:
                best[t["to"]] = t
            if t["type"] in BEAT_MATCHED:
                matched += 1
                if abs(t.get("pre", 1.0) - 1) > 1e-3:
                    bent += 1
                if t.get("m", 1) != 1:
                    halfdouble += 1
        for b in ids:
            if b != a:
                pair_best[best[b]["type"] if b in best else "none"] += 1
        beat = sum(1 for t in best.values() if t["type"] in BEAT_MATCHED)
        per_song.append((beat, len(best), title[a]))
    n_pairs = len(ids) * (len(ids) - 1)
    print(f"{len(ids)} songs, {n_pairs} ordered pairs")
    for k in KINDS:
        print(f"  best link {k:<6} {pair_best[k]:4d}  ({pair_best[k] / n_pairs:.0%})")
    kinds = Counter(t["type"] for a in ids for t in tr.read(a) if t["to"] in title)
    print("stored transitions by kind:", dict(kinds))
    smooth = ("cut", "drop", "blend", "fade", "echo")
    covered = sum(1 for a in ids for b in ids if a != b
                  and any(t["to"] == b and t["type"] in smooth for t in tr.read(a)))
    print(f"pairs with a smooth way across (cut, drop, blend, fade, echo): {covered}/{n_pairs} ({covered / n_pairs:.0%})")
    print(f"beat-matched transitions stored: {matched}  (tempo bent on both sides: {bent}, double/half time: {halfdouble})")
    print("destinations per song (beat-matched / any):")
    for beat, anyn, t in sorted(per_song):
        print(f"  {beat:3d} / {anyn:3d}  {t}")


if __name__ == "__main__":
    main()
