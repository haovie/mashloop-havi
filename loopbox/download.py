"""Music from the web through yt-dlp: YouTube (and YouTube Music), SoundCloud,
Zing MP3, Bandcamp, Mixcloud and Jamendo, single songs and playlists. A JS runtime
(node or deno, MASHLOOP_JS_RUNTIME) solves YouTube's player challenges.

Only links to these sites are accepted, and yt-dlp may only use their
extractors, so the server can never be made to fetch anything else.

Ids: a YouTube video keeps its own 11-character id; a song from any other
site is "x" and 16 hex digits of a hash of the site and its id there, so the
same song is the same track whoever adds it and however it was linked."""
import hashlib
import re
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import yt_dlp

from .config import JS_RUNTIME, MAX_PLAYLIST

BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "js_runtimes": {JS_RUNTIME: {}},
    "socket_timeout": 30,
}

# site -> (hosts, yt-dlp extractors allowed for it)
SOURCES = {
    "youtube": ({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}, [r"youtube.*"]),
    "soundcloud": ({"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com", "on.soundcloud.com"}, [r"soundcloud.*"]),
    "zingmp3": ({"zingmp3.vn", "www.zingmp3.vn", "m.zingmp3.vn", "mp3.zing.vn"}, [r"zingmp3.*"]),
    "bandcamp": ({"bandcamp.com"}, [r"Bandcamp.*"]),  # and every artist.bandcamp.com
    "mixcloud": ({"mixcloud.com", "www.mixcloud.com", "m.mixcloud.com"}, [r"mixcloud.*"]),
    # Creative Commons music: each song carries its licence, credited in the UI.
    "jamendo": ({"jamendo.com", "www.jamendo.com"}, [r"Jamendo.*"]),
}
SITE_NAMES = {"youtube": "YouTube", "soundcloud": "SoundCloud", "zingmp3": "Zing MP3", "bandcamp": "Bandcamp",
              "mixcloud": "Mixcloud", "jamendo": "Jamendo"}


def cc_name(license_url_or_code: str | None) -> str | None:
    """"http://creativecommons.org/licenses/by-nc-sa/3.0/" (or "by-nc-sa") -> "CC BY-NC-SA 3.0"."""
    s = str(license_url_or_code or "").strip()
    m = re.search(r"/licenses/([a-z-]+)(?:/(\d(?:\.\d)?))?", s, re.I) or re.fullmatch(r"([a-z-]+)()", s, re.I)
    if not m or not re.fullmatch(r"by(-(nc|sa|nd))*", m.group(1).lower()):
        return None
    return " ".join(filter(None, ["CC", m.group(1).upper(), m.group(2)]))

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def site_of(url: str) -> str | None:
    try:
        u = urlparse(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https"):
        return None
    for site, (hosts, _) in SOURCES.items():
        if host in hosts or (site == "bandcamp" and host.endswith(".bandcamp.com")):
            return site
    return None


def is_youtube(url: str) -> bool:
    return site_of(url) == "youtube"


def _opts(site: str, **extra) -> dict:
    return {**BASE_OPTS, "allowed_extractors": SOURCES[site][1], **extra}


def track_id(site: str, native_id: str) -> str:
    if site == "youtube":
        return native_id
    return "x" + hashlib.sha1(f"{site}:{native_id}".encode()).hexdigest()[:16]


def _entry(e: dict, site: str = "youtube") -> dict | None:
    native = str(e.get("id") or "")
    if not native or (site == "youtube" and not _VIDEO_ID.match(native)):
        return None
    thumbs = e.get("thumbnails") or []
    thumb = e.get("thumbnail") or (thumbs[-1]["url"] if thumbs else None)
    if site == "youtube":
        url = f"https://www.youtube.com/watch?v={native}"
        thumb = thumb or f"https://i.ytimg.com/vi/{native}/mqdefault.jpg"
    else:
        url = e.get("webpage_url") or e.get("url")
        if not url or site_of(url) != site:
            return None
    title = e.get("title") or e.get("track")
    if not title:  # flat playlist entries may only carry their link: the slug stands in until download
        title = Path(urlparse(url).path).name.replace("-", " ").replace(".html", "").strip() or native
    out = {
        "id": track_id(site, native),
        "source": site,
        "title": title,
        "channel": e.get("artist") or e.get("channel") or e.get("uploader") or "",
        "duration": e.get("duration"),
        "thumbnail": thumb,
        "url": url,
    }
    if site == "jamendo" and cc_name(e.get("license")):
        # A CC licence asks for the artist, the licence and a link back to the song.
        out["credit"] = {"artist": out["channel"], "license": cc_name(e.get("license")), "license_url": None,
                         "page": url, "via": "Jamendo"}
    return out


def search(query: str, n: int = 8) -> list[dict]:
    opts = _opts("youtube", extract_flat="in_playlist", skip_download=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    return [x for x in (_entry(e) for e in info.get("entries") or []) if x]


def resolve(text: str) -> list[dict]:
    """A song link gives one entry, a playlist (set, album) link gives its songs,
    and anything else is a YouTube search for the best match."""
    text = text.strip()
    if not re.match(r"^https?://", text):
        return search(text[:200], 1)
    site = site_of(text)
    if site is None:
        raise ValueError("chỉ nhận link YouTube, SoundCloud, Zing MP3, Bandcamp, Mixcloud hoặc Jamendo")
    opts = _opts(site, extract_flat="in_playlist", skip_download=True, playlistend=MAX_PLAYLIST)
    # A watch URL that also carries &list= is treated as the single video.
    if site == "youtube" and ("watch?" in text or "youtu.be/" in text):
        opts["noplaylist"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(text, download=False)
    if info.get("_type") == "playlist" or "entries" in info:
        return [x for x in (_entry(e, site) for e in info.get("entries") or []) if x][:MAX_PLAYLIST]
    e = _entry(info, site)
    return [e] if e else []


def download(meta: dict, dest_dir: Path, on_progress: Callable[[float], None] | None = None) -> tuple[Path, dict]:
    """Fetches the best audio of a registered track into dest_dir/source.*"""
    site = meta.get("source") or "youtube"
    url = f"https://www.youtube.com/watch?v={meta['id']}" if site == "youtube" else meta["url"]
    if site_of(url) != site:
        raise ValueError("link không thuộc nguồn được hỗ trợ")

    def hook(d):
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    dest_dir.mkdir(parents=True, exist_ok=True)
    last_error = None
    # YouTube intermittently answers 403 for one player client's stream URLs;
    # another client usually gets through.
    for client in ((None, "tv", "web_safari", "mweb") if site == "youtube" else (None,)):
        for stale in dest_dir.glob("source.*"):
            stale.unlink(missing_ok=True)
        opts = _opts(site, format="bestaudio/best", noplaylist=True, outtmpl=str(dest_dir / "source.%(ext)s"),
                     progress_hooks=[hook], retries=3)
        if client:
            opts["extractor_args"] = {"youtube": {"player_client": [client]}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            # Go+ tracks only stream a 30-second preview to everyone else.
            previews = any("preview" in str(f.get("format_id", "")) for f in info.get("requested_downloads") or [])
            if site == "soundcloud" and (previews or (info.get("duration") or 99) <= 31):
                raise RuntimeError("SoundCloud chỉ cho nghe thử 30 giây bài này")
            path = Path(info["requested_downloads"][0]["filepath"])
            return path, _entry(info, site) or {}
        except yt_dlp.utils.DownloadError as e:
            last_error = e
            msg = str(e)
            if "VIP" in msg:
                raise RuntimeError(f"bài này chỉ dành cho tài khoản VIP của {SITE_NAMES[site]}") from None
            if site != "youtube" or ("403" not in msg and "Forbidden" not in msg):
                raise
    raise last_error
