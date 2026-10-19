import { Track } from "./graph.js";

// The library this page works on. Hosted, the server opens a fresh one for
// every visit (nothing is kept in the browser, so a reload starts empty); in
// the personal app the session is simply "local".
export const session = { id: null, multiuser: false, limits: {}, key: null };
let opening = null;
// Called after the server lost the session (it restarted): put the songs back.
export const hooks = { restore: async () => {} };

async function open() {
  if (!opening) {
    opening = fetch("/api/session", { method: "POST" })
      .then(async (r) => {
        if (!r.ok) await fail(r);
        return r.json();
      })
      .then((s) => Object.assign(session, {
        id: s.sid, multiuser: s.multiuser, limits: s.limits || {},
        key: s.k ? Uint8Array.from(atob(s.k), (c) => c.charCodeAt(0)) : null,
      }))
      .finally(() => (opening = null));
  }
  return opening;
}

// Hosted, audio arrives scrambled with this session's key (server.keystream):
// the same 4 KiB block, SHA-256 of key + track id + counter, XORed over it.
async function keystream(tid) {
  const t = new TextEncoder().encode(tid);
  const out = new Uint8Array(4096);
  for (let i = 0; i < 128; i++) {
    const msg = new Uint8Array(session.key.length + t.length + 4);
    msg.set(session.key, 0);
    msg.set(t, session.key.length);
    new DataView(msg.buffer).setUint32(session.key.length + t.length, i, false);
    out.set(new Uint8Array(await crypto.subtle.digest("SHA-256", msg)), i * 32);
  }
  return out;
}

async function unscramble(buf, tid) {
  const ks = new Uint32Array((await keystream(tid)).buffer);
  const words = Math.floor(buf.byteLength / 4);
  const data = new Uint32Array(buf, 0, words);
  for (let i = 0; i < words; i++) data[i] ^= ks[i & 1023];
  const tail = new Uint8Array(buf, words * 4);
  const ksb = new Uint8Array(ks.buffer);
  for (let i = 0; i < tail.length; i++) tail[i] ^= ksb[(words * 4 + i) & 4095];
  return buf;
}

export const ready = () => (session.id ? Promise.resolve(session) : open());

async function sound(id, kind, kid, retry) {
  await ready();
  const r = await fetch(`/api/tracks/${id}/${kind}`, { headers: { "X-Session": session.id } });
  if (r.status === 410 && retry) {
    session.id = null;
    await open();
    await hooks.restore();
    return sound(id, kind, kid, false);
  }
  if (!r.ok) await fail(r);
  const buf = await r.arrayBuffer();
  return r.headers.get("X-Mash") === "1" ? unscramble(buf, kid) : buf;
}

async function fail(r) {
  let msg = `${r.status}`;
  try {
    msg = (await r.json()).detail || msg;
  } catch (_) {
    /* not json */
  }
  throw new Error(msg);
}

async function json(url, opts = {}, retry = true) {
  await ready();
  const r = await fetch(url, { ...opts, headers: { ...(opts.headers || {}), "X-Session": session.id } });
  if (r.status === 410 && retry) {
    session.id = null;
    await open();
    await hooks.restore();
    return json(url, opts, false);
  }
  if (!r.ok) await fail(r);
  return r.json();
}

const send = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  tracks: () => json("/api/tracks"),
  search: (q) => json(`/api/search?q=${encodeURIComponent(q)}`),
  add: (text) => json("/api/tracks", send("POST", { text })),
  restore: (ids) => json("/api/tracks", send("POST", { ids }), false),
  patch: (id, data) => json(`/api/tracks/${id}`, send("PATCH", data)),
  remove: (id) => json(`/api/tracks/${id}`, { method: "DELETE" }),
  reanalyze: (id) => json(`/api/tracks/${id}/reanalyze`, { method: "POST" }),
  relink: () => json("/api/relink", { method: "POST" }),
  analysis: (id) => json(`/api/tracks/${id}/analysis`),
  transitions: (id) => json(`/api/tracks/${id}/transitions`),
  samples: () => json("/api/samples"),
  async audio(id, retry = true) {
    return sound(id, "audio", id, retry);
  },
  // The vocal stem of a sung song (scrambled with its own key, "<id>:v").
  async vocals(id, retry = true) {
    return sound(id, "vocals", `${id}:v`, retry);
  },
  // The file is the request body; XHR because fetch cannot report upload progress.
  async upload(file, onProgress = () => {}) {
    await ready();
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest();
      x.open("POST", "/api/upload");
      x.setRequestHeader("X-Session", session.id);
      x.setRequestHeader("X-Filename", encodeURIComponent(file.name));
      x.setRequestHeader("Content-Type", "application/octet-stream");
      x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      x.onload = () => {
        let body = null;
        try {
          body = JSON.parse(x.responseText);
        } catch (_) {
          /* not json */
        }
        if (x.status >= 200 && x.status < 300) resolve(body);
        else reject(new Error(body?.detail || `${x.status}`));
      };
      x.onerror = () => reject(new Error("mất kết nối"));
      x.send(file);
    });
  },
};

// Fetches and decodes tracks, keeping a few decoded buffers in memory
// (a 4-minute song is ~85 MB of float samples).
export class Loader extends EventTarget {
  constructor(engine, keep = 5) {
    super();
    this.engine = engine;
    this.keep = keep;
    this.cache = new Map(); // id -> {promise, track, used}
    this.metas = new Map();
    this.protect = () => [];
  }

  setMetas(list) {
    this.metas = new Map(list.map((m) => [m.id, m]));
  }

  readyCount() {
    let n = 0;
    for (const m of this.metas.values()) if (m.status === "ready") n++;
    return n;
  }

  has(id) {
    return !!this.cache.get(id)?.track;
  }

  get(id) {
    const hit = this.cache.get(id);
    if (hit) {
      hit.used = performance.now();
      return hit.promise;
    }
    const entry = { track: null, used: performance.now() };
    entry.promise = (async () => {
      const meta = this.metas.get(id) || { id, title: id };
      const [analysis, audio, trans] = await Promise.all([api.analysis(id), api.audio(id), api.transitions(id)]);
      const buffer = await this.engine.ctx.decodeAudioData(audio);
      const track = new Track(meta, analysis, buffer);
      track.transitions = trans.items;
      track.onward = trans.onward || null; // each destination's own best ways on
      entry.track = track;
      return track;
    })();
    entry.promise.catch(() => this.cache.delete(id));
    this.cache.set(id, entry);
    this._evict();
    return entry.promise;
  }

  // New pairs were linked in this library: every loaded song re-reads its exits.
  refreshAll() {
    for (const e of this.cache.values()) if (e.track) this.refreshTransitions(e.track);
  }

  async refreshTransitions(track) {
    try {
      const { items, onward } = await api.transitions(track.id);
      track.transitions = items;
      track.onward = onward || null;
      this.dispatchEvent(new CustomEvent("transitions", { detail: track }));
    } catch (_) {
      /* keep the old list */
    }
  }

  // The vocal stem of a loaded song, decoded (null when it has none), kept as
  // long as the song: fetched only for a transition that hands over by layers.
  vocals(id) {
    if (!this.metas.get(id)?.stems) return Promise.resolve(null);
    const hit = this.cache.get(id);
    if (hit?.vocals) return hit.vocals;
    const p = (async () => {
      const raw = await api.vocals(id);
      return this.engine.ctx.decodeAudioData(raw);
    })();
    p.catch(() => {
      const e = this.cache.get(id);
      if (e && e.vocals === p) e.vocals = null;
    });
    if (hit) hit.vocals = p;
    return p;
  }

  drop(id) {
    this.cache.delete(id);
  }

  _evict() {
    if (this.cache.size <= this.keep) return;
    const keep = new Set(this.protect());
    const order = [...this.cache.entries()].filter(([id, e]) => !keep.has(id) && e.track).sort((a, b) => a[1].used - b[1].used);
    while (this.cache.size > this.keep && order.length) this.cache.delete(order.shift()[0]);
  }
}
