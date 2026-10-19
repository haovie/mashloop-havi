import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MASHLOOP_DATA") or ROOT / "data")
LIBRARY_DIR = DATA_DIR / "library"
# One file per ordered pair of songs: the transitions from the first to the
# second. Shared by every listener, so a pair is only ever computed once.
PAIRS_DIR = DATA_DIR / "pairs"
# Per-song transition lists assembled for the whole local library, read by the
# test tools (tools/sim_player.mjs, link_report.py). Written by `cli relink`.
TRANSITIONS_DIR = DATA_DIR / "transitions"
WEB_DIR = ROOT / "web"


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


HOST = os.environ.get("MASHLOOP_HOST") or "127.0.0.1"
PORT = _env_int("MASHLOOP_PORT", 8765)

# Hosted for many listeners (MASHLOOP_MULTIUSER=1): every visit starts an empty
# library of its own, and songs anyone has added are kept as a shared cache.
# Off (the default) it is the personal app: one library, kept between visits.
MULTIUSER = os.environ.get("MASHLOOP_MULTIUSER") == "1"
# Songs analysed at once (each worker process holds its own models on the GPU)
# and processes computing transitions between songs.
ANALYSIS_WORKERS = _env_int("MASHLOOP_ANALYSIS_WORKERS", 1)
LINK_WORKERS = _env_int("MASHLOOP_LINK_WORKERS", 1)
# CPU threads per worker process (numpy, librosa, torch on the CPU parts).
WORKER_THREADS = _env_int("MASHLOOP_WORKER_THREADS", 4)
# Shared cache size: past it, the songs nobody has used for longest go.
MAX_STORE_GB = float(os.environ.get("MASHLOOP_MAX_STORE_GB") or 0)  # 0 = no limit
MAX_SESSION_TRACKS = _env_int("MASHLOOP_MAX_SESSION_TRACKS", 60)
MAX_PLAYLIST = _env_int("MASHLOOP_MAX_PLAYLIST", 25)
MAX_UPLOAD_MB = _env_int("MASHLOOP_MAX_UPLOAD_MB", 80)
SESSION_TTL_S = _env_int("MASHLOOP_SESSION_TTL_H", 12) * 3600
# Hosted, the sound of a song nobody has used for this long is deleted; what the
# analysis made stays, so adding the song again only fetches the sound (an
# uploaded file has to be uploaded again). The sample library and the songs an
# open library holds are kept.
AUDIO_TTL_S = _env_int("MASHLOOP_AUDIO_TTL_H", 12) * 3600
# JavaScript runtime yt-dlp uses for YouTube's player challenges (node or deno).
JS_RUNTIME = os.environ.get("MASHLOOP_JS_RUNTIME") or "node"

# Audio stored for playback and analysis: identical samples on both sides.
SAMPLE_RATE = 44100
ANALYSIS_SR = 22050
TARGET_LUFS = -14.0
MAX_DURATION_S = _env_int("MASHLOOP_MAX_DURATION_S", 15 * 60)

DEVICE = "cuda"  # falls back to cpu at runtime when CUDA is unavailable

# Bump when the analysis output changes in a way that requires re-analysis.
ANALYSIS_VERSION = 6
# Bump when only the cross-track transition search changes.
TRANSITIONS_VERSION = 10

for d in (LIBRARY_DIR, PAIRS_DIR, TRANSITIONS_DIR):
    d.mkdir(parents=True, exist_ok=True)
