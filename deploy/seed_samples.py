"""Puts a zip of songs on the server as one sample library: uploads them
through the running app (so its workers analyse them side by side), waits
until they are ready, names them a sample library and links them all.
(Creative Commons libraries from Jamendo, ccMixter, Incompetech: seed_cc.py.)
On the box: .venv/bin/python deploy/seed_samples.py <songs.zip> "<name>" ["<blurb>"]"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

BASE = os.environ.get("MASHLOOP_URL", "http://127.0.0.1:18765")
AUDIO = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"}
ROOT = Path(__file__).resolve().parent.parent


def call(method, path, sid=None, body=None, raw=None, name=None):
    h = {}
    if sid:
        h["X-Session"] = sid
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    if raw is not None:
        data, h["Content-Type"], h["X-Filename"] = raw, "application/octet-stream", urllib.parse.quote(name)
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def main():
    src, name = sys.argv[1], sys.argv[2]
    with zipfile.ZipFile(src) as z:
        songs = [(Path(i.filename).name, z.read(i)) for i in z.infolist()
                 if not i.is_dir() and Path(i.filename).suffix.lower() in AUDIO]
    print(f"{len(songs)} songs in {src}")
    ids, sids = [], []
    for k in range(0, len(songs), 10):  # a library holds 10 songs
        sid = call("POST", "/api/session")["sid"]
        sids.append(sid)
        for fname, blob in songs[k:k + 10]:
            r = call("POST", "/api/upload", sid, raw=blob, name=fname)
            ids += [a["id"] for a in r["added"]]
            print(f"  up  {r['added'][0]['id']}  {r['added'][0]['title']}")
    t0 = time.time()
    while True:
        st = [t for sid in sids for t in call("GET", "/api/tracks", sid)["tracks"]]
        done = [t for t in st if t["status"] in ("ready", "error")]
        print(f"\r  {len(done)}/{len(st)} analysed after {time.time() - t0:.0f}s", end="", flush=True)
        if len(done) == len(st):
            break
        time.sleep(3)
    print()
    for t in st:
        if t["status"] == "error":
            print(f"  error {t['id']} {t['title']}: {t['error']}")
    ready = [t for t in ids if any(s["id"] == t and s["status"] == "ready" for s in st)]
    env = dict(os.environ, MASHLOOP_DATA=os.environ.get("MASHLOOP_DATA", str(ROOT / "data")))
    blurb = sys.argv[3] if len(sys.argv) > 3 else ""
    subprocess.run([sys.executable, "-m", "loopbox.cli", "samples", "--name", name, "--blurb", blurb, *ready],
                   cwd=ROOT, env=env, check=True)
    # One library holding all of them links every pair once, for everybody.
    sid = call("POST", "/api/session")["sid"]
    call("POST", "/api/tracks", sid, body={"ids": ready})
    t0 = time.time()
    while (left := call("GET", "/api/tracks", sid)["worker"]["linking"]) > 0:
        print(f"\r  linking, {left} pairs left after {time.time() - t0:.0f}s ", end="", flush=True)
        time.sleep(2)
    print(f"\n{len(ready)} songs ready as the sample library \"{name}\"")


if __name__ == "__main__":
    main()
