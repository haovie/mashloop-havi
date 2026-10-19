"""Seeds the Creative Commons sample libraries on the box: popular songs per
theme from Jamendo (through its API; the client id is read from
data/jamendo.id), remixes from ccMixter and pieces by Kevin MacLeod from
Incompetech. Only licences that allow sharing and changing the music are taken
(no ND), and every song is credited in the UI: artist, licence, a link back.

Songs go in through the running app like a listener's (its workers analyse
them side by side; the app does not rate-limit loopback). Once they are ready,
each library is named and credited, and its songs are linked to each other.

On the box: .venv/bin/python deploy/seed_cc.py [--only pop,rock] [--n 12]
What was taken, from where, under which licence: data/seed_cc.json."""
import argparse
import http.client
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("MASHLOOP_DATA", str(ROOT / "data"))
sys.path.insert(0, str(ROOT))
from loopbox import library  # noqa: E402
from loopbox.config import DATA_DIR  # noqa: E402
from loopbox.download import cc_name, track_id  # noqa: E402

http.client._MAXLINE = 1 << 20  # ccMixter sends a header line over the 64 KiB default
BASE = os.environ.get("MASHLOOP_URL", "http://127.0.0.1:18765")
UA = "MashLoop-seed/1.0 (+https://mashloop.truclv.com; non-commercial sample library)"
MIN_S, MAX_S = 120, 420
PER_ARTIST = 2  # songs one artist may have in a library, so a theme is not one band's album
IN_FLIGHT = 24  # songs being fetched or analysed at once (the app turns new ones away past 60)

LIBRARIES = [
    {"key": "pop", "name": "Pop có lời", "blurb": "Giai điệu tươi, giọng hát rõ",
     "jamendo": {"tags": "pop", "vocalinstrumental": "vocal"}},
    {"key": "rock", "name": "Rock", "blurb": "Guitar, trống, hát hết mình",
     "jamendo": {"tags": "rock", "vocalinstrumental": "vocal"}},
    {"key": "hiphop", "name": "Hip-hop", "blurb": "Beat chắc, rap và R&B",
     "jamendo": {"tags": "hiphop"}},
    {"key": "electro", "name": "Electro & Dance", "blurb": "House, EDM, để mix liền nhịp",
     "jamendo": {"tags": "electro"}},
    {"key": "chill", "name": "Chill & Lofi", "blurb": "Nhẹ, chậm, nghe khi làm việc",
     "jamendo": {"fuzzytags": "chillout+lofi+downtempo+chillhop"}},
    {"key": "acoustic", "name": "Acoustic", "blurb": "Mộc: guitar thùng và giọng hát",
     "jamendo": {"tags": "acoustic", "vocalinstrumental": "vocal"}},
    {"key": "ccmixter", "name": "Remix ccMixter", "blurb": "Remix của cộng đồng ccMixter, có giọng hát",
     "ccmixter": {"tags": "female_vocals"}},
    {"key": "kevin-macleod", "name": "Kevin MacLeod", "blurb": "Nhạc nền quen tai từ Incompetech", "one_artist": True,
     "incompetech": ["Sneaky Snitch", "Monkeys Spinning Monkeys", "Fluffing a Duck", "Investigations", "Carefree",
                     "Wallpaper", "Pixel Peeker Polka - faster", "Cipher", "Funkorama", "Hitman", "Life of Riley",
                     "Scheming Weasel (faster version)", "Merry Go", "The Cannery", "Vivacity", "Volatile Reaction",
                     "Hyperfun", "Local Forecast - Elevator", "Easy Lemon", "Voxel Revolution"]},
]


# ------------------------------------------------------------------- the web

def fetch(url: str, timeout: int = 120, referer: str | None = None) -> bytes:
    # ccMixter only serves a file to a request coming from the song's own page.
    req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Referer": referer} if referer else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url: str):
    return json.loads(fetch(url, 60).decode("utf-8", "replace"))


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
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def secs(text) -> int:
    parts = [int(p) for p in re.findall(r"\d+", str(text))]
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def https(url: str | None) -> str | None:
    return url.replace("http://", "https://", 1) if url else url


def no_derivatives(license_url: str) -> bool:
    return bool(re.search(r"/licenses/[a-z-]*nd|sampling", license_url or "", re.I))


# --------------------------------------------------------------- the sources

def jamendo(q: dict) -> list[dict]:
    cid = (DATA_DIR / "jamendo.id").read_text().strip()
    params = {"client_id": cid, "format": "json", "limit": 200, "order": "popularity_total", "include": "musicinfo",
              "audioformat": "mp32", "durationbetween": f"{MIN_S}_{MAX_S}", **q}
    for attempt in range(4):  # the API now and then answers an empty page: ask again
        d = fetch_json("https://api.jamendo.com/v3.0/tracks/?" + urllib.parse.urlencode(params))
        if d["headers"]["status"] != "success":
            raise RuntimeError(f"Jamendo: {d['headers'].get('error_message')}")
        if d["results"]:
            break
        time.sleep(5)
    out = []
    for t in d["results"]:
        lic = https(t.get("license_ccurl"))
        if not t.get("audiodownload_allowed") or not cc_name(lic) or no_derivatives(lic):
            continue
        out.append({"src": f"jamendo:{t['id']}", "tid": track_id("jamendo", str(t["id"])), "kind": "link",
                    "url": t["shareurl"], "title": t["name"],
                    "artist": t["artist_name"], "duration": t.get("duration"),
                    "credit": {"artist": t["artist_name"], "license": cc_name(lic), "license_url": lic,
                               "page": t["shareurl"], "via": "Jamendo"}})
    return out


def ccmixter(q: dict) -> list[dict]:
    d = fetch_json("https://ccmixter.org/api/query?" + urllib.parse.urlencode({"f": "json", "sort": "rank", "limit": 150, **q}))
    out = []
    for x in d:
        files = [f for f in x.get("files") or [] if (f.get("file_format_info") or {}).get("default-ext") == "mp3"]
        lic = https(x.get("license_url"))
        if not files or not cc_name(lic) or no_derivatives(lic):
            continue
        dur = secs(files[0]["file_format_info"].get("ps", "0"))
        if not MIN_S <= dur <= MAX_S:
            continue
        artist = x.get("user_real_name") or x.get("user_name") or ""
        out.append({"src": f"ccmixter:{x['upload_id']}", "kind": "file", "url": files[0]["download_url"],
                    "title": x["upload_name"], "artist": artist, "duration": dur,
                    "credit": {"artist": artist, "license": cc_name(lic), "license_url": lic,
                               "page": x.get("file_page_url"), "via": "ccMixter"}})
    return out


def incompetech(titles: list[str]) -> list[dict]:
    pieces = {p["title"].strip().lower(): p for p in fetch_json("https://incompetech.com/music/royalty-free/pieces.json")}
    out = []
    for title in titles:
        p = pieces.get(title.lower())
        if not p or secs(p.get("length")) < 90:
            continue
        url = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/" + urllib.parse.quote(p["filename"])
        out.append({"src": f"incompetech:{p['filename']}", "kind": "file", "url": url, "title": p["title"],
                    "artist": "Kevin MacLeod", "duration": secs(p.get("length")),
                    "credit": {"artist": "Kevin MacLeod", "license": "CC BY 4.0",
                               "license_url": "https://creativecommons.org/licenses/by/4.0/",
                               "page": "https://incompetech.com/music/royalty-free/music.html", "via": "Incompetech"}})
    return out


def candidates(lib: dict) -> list[dict]:
    if "jamendo" in lib:
        return jamendo(lib["jamendo"])
    if "ccmixter" in lib:
        return ccmixter(lib["ccmixter"])
    return incompetech(lib["incompetech"])


# ------------------------------------------------------------------ the app

class Feeder:
    """Brings songs into the app through sessions of 10 (a library's size),
    keeping at most IN_FLIGHT of them being fetched or analysed at once."""

    def __init__(self):
        self.sessions: list[str] = []
        self.room = 0
        self.tids: dict[str, str] = {}  # candidate src -> track id

    def status(self) -> dict[str, dict]:
        return {t["id"]: t for sid in self.sessions for t in call("GET", "/api/tracks", sid)["tracks"]}

    def wait(self, limit: int) -> dict[str, dict]:
        while True:
            st = self.status()
            busy = sum(1 for t in st.values() if t["status"] not in ("ready", "error"))
            if busy <= limit:
                return st
            done = sum(1 for t in st.values() if t["status"] in ("ready", "error"))
            print(f"\r  {done}/{len(st)} analysed, {busy} on their way ", end="", flush=True)
            time.sleep(5)

    def add(self, c: dict) -> str | None:
        self.wait(IN_FLIGHT - 1)
        if self.room <= 0:
            self.sessions.append(call("POST", "/api/session")["sid"])
            self.room = 10
        sid = self.sessions[-1]
        for attempt in range(6):
            try:
                if c["kind"] == "link":
                    r = call("POST", "/api/tracks", sid, body={"text": c["url"]})
                else:
                    blob = fetch(c["url"], 300, referer=c["credit"].get("page"))
                    r = call("POST", "/api/upload", sid, raw=blob, name=f"{c['title']}.mp3")
                break
            except urllib.error.HTTPError as e:
                if e.code == 503:  # the app is busy: wait for it
                    time.sleep(30)
                    continue
                print(f"\n  skip {c['src']} {c['title']}: HTTP {e.code} {e.read()[:200]!r}")
                return None
            except Exception as e:
                print(f"\n  skip {c['src']} {c['title']}: {type(e).__name__} {e}")
                return None
        else:
            return None
        if not r.get("added"):
            return None
        self.room -= 1
        tid = r["added"][0]["id"]
        self.tids[c["src"]] = tid
        print(f"\n  in  {tid}  {c['artist'][:24]} - {c['title'][:40]}  ({c['credit']['license']}, {c['credit']['via']})")
        return tid


def norm(c: dict) -> str:
    return re.sub(r"\W+", " ", f"{c['artist']} {c['title']}".lower()).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="library keys, comma separated (default: all)")
    ap.add_argument("--n", type=int, default=12, help="songs per library")
    ap.add_argument("--spare", type=int, default=3, help="extra songs fetched per library, for those that fail")
    args = ap.parse_args()
    only = {k for k in args.only.split(",") if k}
    libs = [lib for lib in LIBRARIES if not only or lib["key"] in only]

    # Choose: the most popular songs of each theme, none in two libraries (nor in
    # a library this run leaves alone, such as an uploaded one).
    keys = {lib["key"] for lib in libs}
    seen = {t for lib in library.samples()["libraries"] if lib["key"] not in keys for t in lib["ids"]}
    taken, seen_names = {}, set()
    for lib in libs:
        pool = []
        try:
            found = candidates(lib)
        except Exception as e:
            print(f"{lib['name']}: source failed ({type(e).__name__}: {e}), skipped")
            found = []
        per_artist: dict[str, int] = {}
        for c in found:
            if norm(c) in seen_names or c.get("tid") in seen:
                continue
            artist = c["artist"].strip().lower()
            if not lib.get("one_artist") and per_artist.get(artist, 0) >= PER_ARTIST:
                continue
            per_artist[artist] = per_artist.get(artist, 0) + 1
            pool.append(c)
            seen_names.add(norm(c))
            if len(pool) >= args.n + args.spare:
                break
        taken[lib["key"]] = pool
        print(f"{lib['name']}: {len(pool)} chosen")

    feeder = Feeder()
    for lib in libs:
        for c in taken[lib["key"]]:
            feeder.add(c)
    st = feeder.wait(0)
    print()

    # The report keeps the libraries this run left alone.
    report = library.read_json(DATA_DIR / "seed_cc.json") or {}
    report = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "libraries": [lib for lib in report.get("libraries") or [] if lib.get("key") not in keys]}
    for lib in libs:
        ready = []
        for c in taken[lib["key"]]:
            tid = feeder.tids.get(c["src"])
            t = st.get(tid) if tid else None
            if not t or t["status"] != "ready":
                if t:
                    print(f"  not ready {tid} {c['title']}: {t.get('error')}")
                continue
            if tid in seen and tid not in ready:  # already in another library (the same file twice)
                continue
            library.update(tid, title=c["title"], channel=c["artist"], credit=c["credit"])
            ready.append(tid)
            seen.add(tid)
            if len(ready) >= args.n:
                break
        library.set_sample_library(lib["key"], lib["name"], ready, lib["blurb"])
        report["libraries"].append({"key": lib["key"], "name": lib["name"], "songs": [
            {"id": feeder.tids[c["src"]], "title": c["title"], "artist": c["artist"], **c["credit"]}
            for c in taken[lib["key"]] if feeder.tids.get(c["src"]) in ready]})
        print(f"{lib['name']}: {len(ready)} songs")
    library.write_json(DATA_DIR / "seed_cc.json", report)

    # Each library links its own songs once, for everybody.
    for lib in library.samples()["libraries"]:
        if only and lib["key"] not in only:
            continue
        sid = call("POST", "/api/session")["sid"]
        call("POST", "/api/tracks", sid, body={"ids": lib["ids"]})
        t0 = time.time()
        while (left := call("GET", "/api/tracks", sid)["worker"]["linking"]) > 0:
            print(f"\r  {lib['name']}: linking, {left} pairs left after {time.time() - t0:.0f}s ", end="", flush=True)
            time.sleep(3)
        print(f"\r  {lib['name']}: linked ({len(lib['ids'])} songs)                    ")
    print("done")


if __name__ == "__main__":
    main()
