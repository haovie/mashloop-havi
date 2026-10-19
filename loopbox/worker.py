"""Background work, in parallel. Songs are analysed in a pool of worker
processes (ANALYSIS_WORKERS, each holding its own models on the GPU; downloads
run inside the same jobs, so several fetch at once), and transitions between
songs are computed in a second pool (LINK_WORKERS). The server process only
schedules: it knows what is on its way and tells whoever is listening when a
song is ready or a pair of songs is linked."""
import multiprocessing as mp
import os
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Callable

from . import library
from .config import ANALYSIS_VERSION, ANALYSIS_WORKERS, LINK_WORKERS, MAX_STORE_GB, TRANSITIONS_DIR, \
    TRANSITIONS_VERSION, WORKER_THREADS

LINK_CHUNK = 6  # songs per linking job, so several link workers share a big library

_lock = threading.RLock()
_pools: dict[str, ProcessPoolExecutor] = {}
_ingesting: dict[str, int] = {}  # track id -> attempts
_linking: set[tuple[str, str]] = set()  # ordered pairs being computed
_relinking = {"on": False}
on_ready: list[Callable[[str], None]] = []  # called with a track id once it is analysed
on_linked: list[Callable[[set[str]], None]] = []  # called with the songs whose pairs were added
_evict_state = {"at": 0.0, "running": False}
keep_ids: Callable[[], set[str]] = lambda: set()  # songs that eviction must not touch (set by the server)
# Songs analysed by an older version, analysed again in the background while
# they stay ready (their old analysis serves meanwhile), only when no
# listener's song is waiting, and leaving a worker free for one.
_upgrade_queue: list[str] = []
_upgrading: set[str] = set()
UPGRADE_SLOTS = max(1, ANALYSIS_WORKERS - 1)


# ------------------------------------------------------------ in the children

def _init_child(uses_torch: bool) -> None:
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
        os.environ[var] = str(WORKER_THREADS)
    if uses_torch:
        import torch
        torch.set_num_threads(WORKER_THREADS)


def _ingest_job(tid: str) -> str:
    from . import ingest
    return ingest.process(tid)


def _upgrade_job(tid: str) -> bool:
    from . import ingest
    return ingest.upgrade(tid)


def _link_job(tid: str, others: list[str]) -> int:
    from .analysis import transitions
    return transitions.link(tid, others)


def _relink_job() -> None:
    from .analysis import transitions
    transitions.relink_all()


# ------------------------------------------------------------- in the server

def _pool(kind: str) -> ProcessPoolExecutor:
    with _lock:
        pool = _pools.get(kind)
        if pool is None:
            n = ANALYSIS_WORKERS if kind == "ingest" else LINK_WORKERS
            # spawn, not fork: CUDA cannot be shared with a forked child.
            pool = ProcessPoolExecutor(max_workers=max(1, n), mp_context=mp.get_context("spawn"),
                                       initializer=_init_child, initargs=(kind == "ingest",))
            _pools[kind] = pool
        return pool


def _reset_pool(kind: str, broken: ProcessPoolExecutor) -> None:
    """A worker process died (a native crash, the OOM killer): the pool is
    unusable, so a fresh one takes its place."""
    with _lock:
        if _pools.get(kind) is broken:
            _pools.pop(kind)
            broken.shutdown(wait=False, cancel_futures=True)


def enqueue_ingest(tid: str) -> None:
    with _lock:
        if tid in _ingesting:
            return
        _ingesting[tid] = _ingesting.get(tid, 0)
        _submit_ingest(tid)


def _submit_ingest(tid: str) -> None:
    pool = _pool("ingest")
    fut = pool.submit(_ingest_job, tid)
    fut.add_done_callback(lambda f: _ingest_done(tid, pool, f))


def _ingest_done(tid: str, pool, fut) -> None:
    status = "error"
    try:
        status = fut.result()
    except BrokenProcessPool:
        _reset_pool("ingest", pool)
        with _lock:
            tries = _ingesting.get(tid, 0) + 1
            _ingesting[tid] = tries
        if tries < 2:
            with _lock:
                _submit_ingest(tid)
            return
        library.update(tid, status="error", stage="error", progress=0.0, error="máy phân tích bị lỗi với bài này")
    except Exception:
        traceback.print_exc()
    with _lock:
        _ingesting.pop(tid, None)
    if status == "ready":
        for cb in on_ready:
            try:
                cb(tid)
            except Exception:
                traceback.print_exc()
    _maybe_evict()


def enqueue_links(tid: str, others: list[str]) -> int:
    """Schedules the missing pairs between tid and others (both ways).
    Returns how many songs that covers."""
    from .analysis import transitions
    with _lock:
        need = [o for o in dict.fromkeys(others) if o != tid
                and (tid, o) not in _linking and (o, tid) not in _linking
                and not (transitions.has_pair(tid, o) and transitions.has_pair(o, tid))]
        for o in need:
            _linking.update({(tid, o), (o, tid)})
        for k in range(0, len(need), LINK_CHUNK):
            chunk = need[k:k + LINK_CHUNK]
            pool = _pool("link")
            fut = pool.submit(_link_job, tid, chunk)
            fut.add_done_callback(lambda f, c=chunk, p=pool: _link_done(tid, c, p, f))
    return len(need)


def _link_done(tid: str, chunk: list[str], pool, fut) -> None:
    try:
        fut.result()
    except BrokenProcessPool:
        _reset_pool("link", pool)
    except Exception:
        traceback.print_exc()
    with _lock:
        for o in chunk:
            _linking.discard((tid, o))
            _linking.discard((o, tid))
    for cb in on_linked:
        try:
            cb({tid, *chunk})
        except Exception:
            traceback.print_exc()


def relink_all() -> None:
    """Recomputes every pair (personal mode: the "Tính lại" button)."""
    with _lock:
        if _relinking["on"]:
            return
        _relinking["on"] = True
    fut = _pool("link").submit(_relink_job)

    def done(f):
        try:
            f.result()
        except Exception:
            traceback.print_exc()
        _relinking["on"] = False
        for cb in on_linked:
            cb(set(library.ready_ids()))

    fut.add_done_callback(done)


def queue_upgrades(ids: list[str]) -> None:
    with _lock:
        for t in ids:
            if t not in _upgrading and t not in _upgrade_queue:
                _upgrade_queue.append(t)


def _upgrade_tick() -> None:
    with _lock:
        if _ingesting:  # listeners' songs first
            return
        while len(_upgrading) < UPGRADE_SLOTS and _upgrade_queue:
            tid = _upgrade_queue.pop(0)
            meta = library.read_meta(tid) or {}
            if (meta.get("status") != "ready" or meta.get("analysis_version") == ANALYSIS_VERSION
                    or not library.has_audio(tid)):
                continue
            _upgrading.add(tid)
            pool = _pool("ingest")
            fut = pool.submit(_upgrade_job, tid)
            fut.add_done_callback(lambda f, t=tid, p=pool: _upgrade_done(t, p, f))


def _upgrade_done(tid: str, pool, fut) -> None:
    ok = False
    try:
        ok = bool(fut.result())
    except BrokenProcessPool:
        _reset_pool("ingest", pool)
    except Exception:
        traceback.print_exc()
    with _lock:
        _upgrading.discard(tid)
    if ok:
        # Its transitions were found with the old analysis: found again, and
        # the libraries holding it link it anew.
        library.drop_pairs(tid)
        for cb in on_ready:
            try:
                cb(tid)
            except Exception:
                traceback.print_exc()


def upgrades_left() -> int:
    with _lock:
        return len(_upgrade_queue) + len(_upgrading)


def _upgrade_loop() -> None:
    while True:
        time.sleep(5)
        try:
            _upgrade_tick()
        except Exception:
            traceback.print_exc()


def backlog() -> int:
    """Songs being prepared or waiting for it, all libraries together."""
    with _lock:
        return len(_ingesting)


def status(ids: list[str]) -> dict:
    """What is still on its way for a library: songs being prepared and pairs
    of its songs being linked."""
    s = set(ids)
    with _lock:
        return {
            "processing": sum(1 for t in s if t in _ingesting),
            "linking": sum(1 for a, b in _linking if a in s and b in s) // 2,
            "relink": _relinking["on"],
        }


def _maybe_evict() -> None:
    if MAX_STORE_GB <= 0:
        return
    with _lock:
        if _evict_state["running"] or time.time() - _evict_state["at"] < 60:
            return
        _evict_state["running"] = True

    def run():
        try:
            with _lock:
                busy = set(_ingesting) | set(_upgrading) | {t for pair in _linking for t in pair}
            gone = library.evict(int(MAX_STORE_GB * 1e9), keep_ids() | busy)
            if gone:
                print(f"[mashloop] store over {MAX_STORE_GB} GB: freed the {len(gone)} least used songs")
        except Exception:
            traceback.print_exc()
        finally:
            _evict_state.update(at=time.time(), running=False)

    threading.Thread(target=run, name="mashloop-evict", daemon=True).start()


def _marker_path():
    return TRANSITIONS_DIR / "_version.json"


def stale(metas: list[dict]) -> bool:
    """True when the data on disk was written by newer code than this process
    runs: the server was started before an update and must be restarted."""
    if (library.read_json(_marker_path()) or {}).get("version", 0) > TRANSITIONS_VERSION:
        return True
    return any((m.get("analysis_version") or 0) > ANALYSIS_VERSION for m in metas)


def start_retention(max_idle_s: float, keep: Callable[[], set[str]]) -> None:
    """Every few minutes, deletes the sound of the songs nobody has used for
    max_idle_s (library.drop_idle_audio), except those in keep() and those
    being worked on."""
    def run():
        while True:
            time.sleep(600)
            try:
                with _lock:
                    busy = set(_ingesting) | set(_upgrading) | {t for pair in _linking for t in pair}
                gone = library.drop_idle_audio(max_idle_s, keep() | busy)
                if gone:
                    print(f"[mashloop] deleted the audio of {len(gone)} songs unused for {max_idle_s / 3600:g} h")
            except Exception:
                traceback.print_exc()

    threading.Thread(target=run, name="mashloop-retention", daemon=True).start()


def start() -> None:
    """Resumes interrupted work on startup, and queues the songs analysed by an
    older version for a background upgrade (the sample libraries first); a
    song whose sound was deleted is analysed again when someone adds it back."""
    stale = []
    for m in library.list_tracks():
        if m.get("status") == "ready" and m.get("analysis_version") != ANALYSIS_VERSION:
            if library.has_audio(m["id"]):
                stale.append(m["id"])
        elif m.get("status") not in library.FINAL_STATES:
            enqueue_ingest(m["id"])
    samples = set(library.samples()["ids"])
    queue_upgrades(sorted(stale, key=lambda t: t not in samples))
    threading.Thread(target=_upgrade_loop, name="mashloop-upgrade", daemon=True).start()
