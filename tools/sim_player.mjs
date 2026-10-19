// Fast simulation of the real Player logic (web/js/player.js) against a fake
// audio engine: hours of playback in seconds.
//   node tools/sim_player.mjs [minutes=180] [mode=chain|single] [seed]
// Checks: no exceptions, every beat starts where the previous ended, gain
// automation never overlaps on a voice, no voice leaks, never falls off the
// end of a song, and (chain mode) how widely the library gets visited.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Track } from "../web/js/graph.js";
import { CRITERIA, keyFit, Player, setPhase, tempoGap } from "../web/js/player.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LIB = path.join(ROOT, "data", "library");
const minutes = +(process.argv[2] || 180);
const mode = process.argv[3] || "chain";
let seed = +(process.argv[4] || 1);
Math.random = () => {
  seed = (seed * 1664525 + 1013904223) % 4294967296;
  return seed / 4294967296;
};

const read = (p) => JSON.parse(fs.readFileSync(p, "utf8"));
// SIM_IDS=<file with one id per line>: simulate that set only (a big hosted
// store holds songs whose transitions were never exported).
const only = process.env.SIM_IDS ? new Set(fs.readFileSync(process.env.SIM_IDS, "utf8").split(/\s+/).filter(Boolean)) : null;
const metas = fs.readdirSync(LIB).filter((d) => !only || only.has(d))
  .map((d) => ({ ...read(path.join(LIB, d, "meta.json")),
    stems: ["vocals.mp3", "vocals.flac"].some((f) => fs.existsSync(path.join(LIB, d, f))) }))
  .filter((m) => m.status === "ready");

// ---------------------------------------------------------------- fake audio
const problems = [];
let now = 0;
class FakeParam {
  constructor(voice, name) {
    this.voice = voice;
    this.name = name;
    this.value = name === "gain" ? 0 : 1;
    this.curves = [];
  }
  setValueAtTime() {}
  linearRampToValueAtTime() {}
  cancelScheduledValues() {
    this.curves = [];
  }
  setValueCurveAtTime(values, when, dur) {
    for (const [a, b] of this.curves) {
      if (when < b - 1e-6 && when + dur > a + 1e-6) {
        problems.push(`overlapping ${this.name} curve on ${this.voice.track.id} at ${when.toFixed(3)}`);
        throw new Error("overlap");
      }
    }
    this.curves.push([when, when + dur]);
  }
}
class FakeVoice {
  constructor(engine, track) {
    this.engine = engine;
    this.track = track;
    this.gain = { gain: new FakeParam(this, "gain") };
    this.fx = { gain: new FakeParam(this, "fx") };
    this.level = { low: 1, mid: 1, high: 1 };
    this.bandAt = { low: -Infinity, mid: -Infinity, high: -Infinity };
    this.started = null;
    this.stretched = false;
    this.semis = 0;
    this.shift = 0;
    this.stopTime = Infinity;
  }
  loop(when, from, to, rate, until) {
    fxLog.loop++;
    if (!(to > from + 0.05 && from >= 0 && until > when && rate > 0.8 && rate < 1.25)) {
      problems.push(`loop ${from}-${to} from ${when} to ${until} at ${rate} on ${this.track.id}`);
    }
  }
  // EQ moves on one band must come in time order and stay in range (dB).
  _band(name, when, db) {
    if (!(name in this.level)) problems.push(`unknown band ${name}`);
    if (!(db <= 6 && db >= -100)) problems.push(`band ${name} to ${db} dB`);
    if (when < this.bandAt[name] - 1e-6) problems.push(`band ${name} moved back in time on ${this.track.id} at ${when.toFixed(3)}`);
    this.bandAt[name] = when;
  }
  setBand(name, when, db) {
    this._band(name, when, db);
  }
  rampBand(name, when, db, dur) {
    if (!(dur > 0)) problems.push(`band ramp of ${dur}s`);
    this._band(name, when, db);
    this.bandAt[name] = when + dur;
  }
  hpSweep(when, fromHz, toHz, dur) {
    if (!(fromHz > 0 && toHz > fromHz && dur > 0)) problems.push(`bad hp sweep ${fromHz}->${toHz} in ${dur}`);
  }
  attachVocals(buffer, when, offset) {
    if (!buffer) problems.push(`vocals attached without a stem on ${this.track.id}`);
    if (offset < -1e-6) problems.push(`bad vocal offset ${offset}`);
    this.hasVocals = true;
  }
  vocals(when, mode) {
    if (!["full", "instrumental", "acapella"].includes(mode)) problems.push(`vocal mode ${mode}`);
  }
  dispose() {
    this.engine.voices.delete(this);
  }
  start(when, offset) {
    this.started = when;
    if (offset < -1e-6 || offset > this.track.end + 1e-3) problems.push(`bad offset ${offset} on ${this.track.id}`);
  }
  startSilent(when, offset) {
    this.start(when, offset);
  }
  setRate(when, rate, pos, semis) {
    if (!(rate > 0.78 && rate < 1.27)) problems.push(`rate ${rate}`);
    if (semis != null) {
      if (!this.stretched) problems.push(`pitch set on a plain voice of ${this.track.id}`);
      if (!(Math.abs(semis) <= 2)) problems.push(`semis ${semis}`);
      this.semis = semis;
    }
  }
  gainCurve(v, when, dur) {
    try {
      this.gain.gain.setValueCurveAtTime(v, when, dur);
    } catch (_) {
      /* recorded */
    }
  }
  fadeOut(when, dur) {
    this.gainCurve(null, when, dur);
    this.stopAt(when + dur + 0.02);
  }
  stopAt(t) {
    this.stopTime = Math.min(this.stopTime, t);
  }
  bass() {}
  sweep(when, fromHz, toHz, dur) {
    if (!(fromHz > 0 && toHz > 0 && dur > 0)) problems.push(`bad sweep ${fromHz}->${toHz} in ${dur}`);
  }
  brake(t0, t1, from) {
    if (!(from > 0.8 && from < 1.2)) problems.push(`brake from rate ${from}`);
    this.gainCurve(null, t0 + (t1 - t0) * 0.5, (t1 - t0) * 0.5);
    this.stopAt(t1 + 0.02);
  }
  release(t) {
    this.stopTime = t;
  }
}
const fxLog = { echo: 0, roll: 0, wash: 0, riser: 0, impact: 0, spin: 0, gate: 0, flanger: 0, loop: 0, shifted: 0 };
const engine = {
  ctx: { state: "running", resume: async () => {}, suspend: async () => {} },
  get now() {
    return now;
  },
  audibleTime: () => now,
  voices: new Set(),
  voice(track) {
    const v = new FakeVoice(this, track);
    this.voices.add(v);
    return v;
  },
  canStretch: true,
  stretched: 0,
  async stretchVoice(track, from, to, vocals, semis = 0) {
    if (!(to > from && from >= 0)) problems.push(`stretch window ${from}-${to} on ${track.id}`);
    if (!(Math.abs(semis) <= 2 && Number.isInteger(semis))) problems.push(`stretch shifted by ${semis}`);
    const v = new FakeVoice(this, track);
    v.stretched = true;
    v.semis = semis;
    v.hasVocals = !!vocals;
    this.stretched++;
    if (semis) fxLog.shifted++;
    this.voices.add(v);
    return v;
  },
  riser(start, end) {
    fxLog.riser++;
    if (!(end > start + 1)) problems.push(`riser ${start}-${end}`);
  },
  impact() {
    fxLog.impact++;
  },
  spinback(voice, when, pos, dur, rate) {
    fxLog.spin++;
    if (!(dur > 0.2 && pos > 0 && rate > 0.8 && rate < 1.25)) problems.push(`spinback at ${pos} for ${dur} at ${rate}`);
    voice.fadeOut(when - 0.004, 0.008);
  },
  gate(voice, times) {
    fxLog.gate++;
    for (let k = 1; k < times.length; k++) if (!(times[k] > times[k - 1])) problems.push(`gate beat times not rising at ${times[k]}`);
  },
  flanger(voice, start, end) {
    fxLog.flanger++;
    if (!(end > start)) problems.push(`flanger ${start}-${end}`);
  },
  stopAll() {
    for (const v of this.voices) v.release(now);
  },
  echoOut(voice, start, end, delay) {
    fxLog.echo++;
    if (!(end > start && delay > 0.05 && delay < 2)) problems.push(`echo window ${start}-${end} delay ${delay}`);
  },
  wash(voice, start, end) {
    fxLog.wash++;
    if (!(end > start)) problems.push(`wash window ${start}-${end}`);
  },
  roll(track, slices) {
    fxLog.roll++;
    let t = slices[0].when;
    for (const s of slices) {
      if (Math.abs(s.when - t) > 1e-6 || !(s.dur > 0.01)) problems.push(`roll slice gap at ${s.when}`);
      t = s.when + s.dur;
    }
  },
};

// ------------------------------------------------------------------- loader
const cache = new Map();
const loader = {
  metas: new Map(metas.map((m) => [m.id, m])),
  readyCount: () => metas.length,
  has: (id) => cache.has(id),
  async get(id) {
    if (!cache.has(id)) {
      const meta = metas.find((m) => m.id === id);
      const t = new Track(meta, read(path.join(LIB, id, "analysis.json")), null);
      const tp = path.join(ROOT, "data", "transitions", `${id}.json`);
      const data = fs.existsSync(tp) ? read(tp) : { items: [] };
      t.transitions = data.items;
      t.onward = data.onward || null;
      cache.set(id, t);
    }
    return cache.get(id);
  },
  refreshTransitions() {},
  vocals: (id) => Promise.resolve(metas.find((m) => m.id === id)?.stems ? { stem: id } : null),
};

// --------------------------------------------------------------------- run
const player = new Player(engine, loader);
player.settings.mode = mode;
if (process.argv[5]) player.settings.keep = +process.argv[5];
if (process.argv[6]) player.settings.waitCost = +process.argv[6];
// CRITERION=mood|tempo|key|set|free picks what the chain looks for (default balanced).
if (process.env.CRITERION) player.settings.criterion = process.env.CRITERION;
// AHEAD=0: no looking one song ahead (to compare with).
if (process.env.AHEAD === "0") for (const c of Object.values(CRITERIA)) c.ahead = 0;
// ALL_KINDS=1 switches on every kind of transition, the abrupt ones too.
if (process.env.ALL_KINDS) for (const k of Object.keys(player.settings.allow)) player.settings.allow[k] = true;
player.settings.dwellMin = 60;
player.settings.dwellMax = 150;
player.enabled = () => true;
// Plan history per visit, to explain long waits (DEBUG_WAIT=1 prints them).
let planLog = [];
// Per hand-over, as planned: how well the chain could carry on from the song
// it chose (one step ahead), and where its energy sat against the set's target.
const handovers = [];
let planned = null;
// Hand-overs by the energy where they leave and where they enter (calm, mid,
// driving: the terciles of the sample libraries' exits, 0.51 and 0.72).
const energyClass = (x) => (x < 0.51 ? 0 : x < 0.72 ? 1 : 2);
const moves = new Array(9).fill(0);
const origSet = player._setPlan.bind(player);
player._setPlan = (T, best, prev, t, picked) => {
  planned = {
    onward: player._onwardScore(T, best.tr.to),
    rank: best.tr.ie != null ? player._energyRank(T, best.tr.ie) : null,
    target: setPhase(player.stats.transitions).target,
  };
  origSet(T, best, prev, t, picked);
  planLog.push(`plan ${best.tr.type}->${best.tr.to.slice(0, 5)} q=${best.tr.q} at ${t.toFixed(1)} eta ${(player.plan.eta - t).toFixed(1)}s`);
};
const origDrop = player._dropPlan.bind(player);
player._dropPlan = () => {
  if (player.plan) planLog.push(`drop ${player.plan.tr.type} at ${now.toFixed(1)} target=${!!player.plan.target}`);
  origDrop();
};
player.addEventListener("track", () => {
  const h = player.history;
  if (process.env.DEBUG_WAIT && h.length > 1) {
    const stay = h[h.length - 1].at - h[h.length - 2].at;
    if (stay > player.settings.dwellMax + 30) console.log(`\nlong stay ${stay.toFixed(0)}s in ${h[h.length - 2].id}:\n  ${planLog.slice(-8).join("\n  ")}`);
  }
  planLog = [];
});
await player.start(metas[0].id);
clearInterval(player.timer);
player.timer = 1;

const end = minutes * 60;
let last = null;
let maxDev = 0;
let steps = 0;
let fellOff = 0;
let rapid = 0;
const jumpGaps = [];
const leaveWaits = [];
const moodsTaken = [];
const changes = []; // per song change: beat kept?, tempo gap (octaves), key fit
let songStart = null;
let lastJumpT = -Infinity;
const counts = {};
const styles = {};
let keylocked = 0;
let ramped = 0;
// The walk stuck near the end: jumps back from the last branch point with no
// other jump between them (the longest such run, in jumps and in seconds).
let endRun = 0, endRunFrom = null, endRunMax = 0, endRunSecs = 0;
// SEEKS=1: the listener taps the ring every 97 s; each seek must land on the
// next beat with the meter carrying on.
const seeks = { asked: 0, landed: 0, inMeter: 0, refused: 0 };
let nextSeek = 97;
let rampedLocked = 0;
const seconds = new Map();
let lastLog = 0;
const step = 0.04;
while (now < end) {
  now += step;
  if (process.env.SEEKS && now >= nextSeek) {
    nextSeek += 97;
    seeks.asked++;
    if (!player.seek(Math.floor(Math.random() * player.cur.track.n))) seeks.refused++;
  }
  try {
    player._tick();
  } catch (e) {
    problems.push(`exception at ${now.toFixed(1)}s: ${e.stack}`);
    break;
  }
  await Promise.resolve(); // let loader promises settle like the event loop would
  const tl = player.timeline;
  for (let k = Math.max(0, tl.length - 64); k < tl.length; k++) {
    const e = tl[k];
    if (last && e.t <= last.t) continue;
    if (last && last.kind !== "start") {
      const expected = last.t + last.track.dur[last.beat] / last.rate;
      maxDev = Math.max(maxDev, Math.abs(e.t - expected));
    }
    if (e.kind === "jump" && last && e.beat === 0 && last.beat === last.track.n - 1) fellOff++;
    if (e.kind === "jump" && e.track.lbp >= 0 && e.from >= e.track.lbp && !e.seek) {
      if (endRun++ === 0) endRunFrom = e.t;
      if (endRun > endRunMax) endRunMax = endRun;
      endRunSecs = Math.max(endRunSecs, e.t - endRunFrom);
    } else if (!["seq", "blend", "fxBeat"].includes(e.kind)) {
      endRun = 0;
    }
    if (e.kind === "jump" && e.seek) {
      seeks.landed++;
      const pos = e.track.analysis.pos;
      if (!pos || e.from + 1 >= e.track.n || pos[e.beat] === pos[e.from + 1]) seeks.inMeter++;
    }
    if (e.kind === "jump") {
      if (e.t - lastJumpT < 1.5) rapid++;
      if (isFinite(lastJumpT)) jumpGaps.push(e.t - lastJumpT);
      lastJumpT = e.t;
    } else if (!["seq", "blend", "fxBeat"].includes(e.kind)) {
      lastJumpT = -Infinity; // a song change restarts the pacing
    }
    // A transition starts (blend start, cut or fade): how long after the
    // minimum stay did it begin? (The song counts as entered when the player
    // enters it: at a cut/fade, or at the end of a blend.)
    if (["blendStart", "fxStart", "cut", "drop", "fade"].includes(e.kind) && songStart !== null) {
      leaveWaits.push(e.t - songStart - player.settings.dwellMin);
      if (planned) handovers.push(planned);
      planned = null;
      if (e.tr?.oe != null && e.tr?.ie != null) moves[energyClass(e.tr.oe) * 3 + energyClass(e.tr.ie)]++;
      if (e.tr) {
        moodsTaken.push(e.tr.mood ?? 0);
        const from = loader.metas.get(e.fromTrack ?? e.track.id), to = loader.metas.get(e.tr.to);
        changes.push({
          matched: ["cut", "drop", "blend"].includes(e.tr.type),
          gap: tempoGap(from?.bpm, to?.bpm, e.tr),
          key: keyFit(from?.camelot, to?.camelot, e.tr.semis ?? 0),
        });
      }
    }
    if (["start", "promote", "cut", "drop", "fade", "echo", "wash", "brake", "roll", "riser", "spin", "gate", "flanger",
      "gap"].includes(e.kind)) songStart = e.t;
    counts[e.kind] = (counts[e.kind] || 0) + 1;
    if (e.kind === "blendStart" && e.tr) styles[e.tr.style || "?"] = (styles[e.tr.style || "?"] || 0) + 1;
    if (e.tr && ["blendStart", "cut", "drop"].includes(e.kind) && e.tr.keylock) keylocked++;
    if (e.kind === "blendStart" && e.tr && Math.abs((e.tr.pre || 1) - 1) > 0.004) {
      ramped++;
      if (Math.abs(e.rate - 1) > 0.003) rampedLocked++; // A did ramp toward B (on its key-locked voice)
    }
    seconds.set(e.track.id, (seconds.get(e.track.id) || 0) + e.track.dur[e.beat] / e.rate);
    last = e;
    steps++;
  }
  // Voices still sounding (not yet stopped).
  let live = 0;
  for (const v of engine.voices) if (v.started !== null && v.started <= now && v.stopTime > now) live++;
  if (live > 3) {
    const msg = `${live} live voices at ${now.toFixed(1)}s`;
    if (!problems.some((p) => p.includes("live voices"))) {
      const vs = [...engine.voices].filter((v) => v.stopTime > now).map((v) => `${v.track.id}@${v.started?.toFixed(2)}-${v.stopTime.toFixed(2)}`);
      const ev = player.timeline.slice(-40).filter((e) => e.kind !== "seq" && e.kind !== "blend").map((e) => `${e.kind}@${e.t.toFixed(2)}`);
      problems.push(`${msg}: voices ${vs.join(", ")} | recent ${ev.join(" ")}`);
    } else problems.push(msg);
  }
  for (const v of [...engine.voices]) if (v.stopTime < now - 1) engine.voices.delete(v);
  if (now - lastLog > 1800) {
    lastLog = now;
    process.stdout.write(`  ${Math.round(now / 60)} min, ${player.stats.transitions} transitions\r`);
  }
}

const title = (id) => (metas.find((m) => m.id === id)?.title || id).slice(0, 38);
console.log(`\nsimulated ${minutes} min in ${mode} mode over ${metas.length} tracks`);
console.log(`steps ${steps}, max schedule deviation ${(maxDev * 1000).toFixed(4)} ms, fell off the end ${fellOff}x`);
console.log(`events`, counts);
console.log(`blend styles`, styles, `| key-locked hand-overs ${keylocked} (stretch voices prepared ${engine.stretched})`);
console.log(`tempo ramps ${ramped} (A ramped on its key-locked voice in ${rampedLocked}), effects and engine calls`,
  JSON.stringify(fxLog));
console.log(`jumps ${player.stats.jumps} (${(player.stats.jumps / minutes).toFixed(1)}/min), back-to-back (<1.5 s apart) ${rapid}, transitions ${player.stats.transitions}`);
console.log(`stuck near the end: longest run ${endRunMax} jumps back from the end, ${endRunSecs.toFixed(0)} s`);
const nMoves = moves.reduce((a, b) => a + b, 0);
if (nMoves) {
  const pc = (k) => `${Math.round((100 * moves[k]) / nMoves)}%`.padStart(4);
  const NAMES = ["calm", "mid", "drive"];
  console.log(`energy moves (from \\ to calm mid drive) over ${nMoves}: ` +
    NAMES.map((n, i) => `${n} ${pc(3 * i)} ${pc(3 * i + 1)} ${pc(3 * i + 2)}`).join(" | "));
}
if (handovers.length) {
  const mean = (a) => a.reduce((s, x) => s + x, 0) / Math.max(1, a.length);
  const on = handovers.map((h) => h.onward);
  const dead = on.filter((x) => x < 0.3).length;
  const withE = handovers.filter((h) => h.rank != null);
  const tg = withE.map((h) => h.target), rk = withE.map((h) => h.rank);
  const mt = mean(tg), mr = mean(rk);
  const cov = mean(withE.map((h) => (h.target - mt) * (h.rank - mr)));
  const sd = (a, m) => Math.sqrt(mean(a.map((x) => (x - m) ** 2)));
  const corr = cov / Math.max(1e-9, sd(tg, mt) * sd(rk, mr));
  const breathers = withE.filter((h) => h.target < 0.3);
  console.log(`one step ahead: mean onward quality ${mean(on).toFixed(2)}, dead ends (under 0.3) ${dead} of ${on.length}`);
  console.log(`set energy: off target by ${mean(withE.map((h) => Math.abs(h.rank - h.target))).toFixed(2)} on average, ` +
    `correlation with the target ${corr.toFixed(2)}, breathers landed in the calmest third ` +
    `${breathers.filter((h) => h.rank < 0.34).length} of ${breathers.length}`);
}
if (process.env.SEEKS) {
  console.log(`seeks: asked ${seeks.asked}, refused ${seeks.refused} (mid hand-over), landed ${seeks.landed}, meter kept ${seeks.inMeter}`);
  if (seeks.landed !== seeks.asked - seeks.refused) problems.push(`seeks landed ${seeks.landed} of ${seeks.asked - seeks.refused}`);
}
const via = {};
for (const h of player.history) via[h.via] = (via[h.via] || 0) + 1;
console.log(`last ${player.history.length} song changes by kind`, via);
// How long each song was kept, against the minimum: with "nearest good exit"
// most visits should end shortly after dwellMin.
const stays = [];
for (let k = 1; k < player.history.length; k++) stays.push(player.history[k].at - player.history[k - 1].at);
if (stays.length) {
  stays.sort((a, b) => a - b);
  const q = (p) => stays[Math.min(stays.length - 1, Math.floor(p * stays.length))].toFixed(0);
  const wait = stays.map((s) => s - player.settings.dwellMin).sort((a, b) => a - b);
  const w = (p) => wait[Math.min(wait.length - 1, Math.floor(p * wait.length))].toFixed(0);
  console.log(`time per visit (dwellMin ${player.settings.dwellMin}s, dwellMax ${player.settings.dwellMax}s): p10 ${q(0.1)}s p50 ${q(0.5)}s p90 ${q(0.9)}s max ${q(1)}s`);
  console.log(`  wait after the minimum: p50 ${w(0.5)}s p90 ${w(0.9)}s`);
}
if (moodsTaken.length) {
  // Compare with what a mood-blind chain would take: the mean over all pairs.
  const all = [];
  for (const m of metas) {
    const tp = path.join(ROOT, "data", "transitions", `${m.id}.json`);
    if (!fs.existsSync(tp)) continue;
    const seen = new Set();
    for (const t of read(tp).items) if (!seen.has(t.to)) (seen.add(t.to), all.push(t.mood ?? 0));
  }
  const mean = (a) => a.reduce((s, x) => s + x, 0) / Math.max(1, a.length);
  console.log(`mood distance per song change: ${mean(moodsTaken).toFixed(2)} (all pairs: ${mean(all).toFixed(2)})`);
  const pct = (f) => `${Math.round((100 * changes.filter(f).length) / Math.max(1, changes.length))}%`;
  console.log(`criterion ${player.settings.criterion}: beat kept ${pct((c) => c.matched)}, ` +
    `tempo gap ${(100 * (2 ** mean(changes.map((c) => c.gap)) - 1)).toFixed(1)}% mean, ` +
    `keys compatible ${pct((c) => c.key >= 0.8)}, songs played ${seconds.size}/${metas.length}`);
}
if (leaveWaits.length) {
  leaveWaits.sort((a, b) => a - b);
  const g = (p) => leaveWaits[Math.min(leaveWaits.length - 1, Math.floor(p * leaveWaits.length))].toFixed(1);
  console.log(`transition starts after the minimum stay: p10 ${g(0.1)}s p50 ${g(0.5)}s p90 ${g(0.9)}s max ${g(1)}s`);
}
// Pacing of in-song jumps (seconds between consecutive jumps).
if (jumpGaps.length) {
  jumpGaps.sort((a, b) => a - b);
  const g = (p) => jumpGaps[Math.min(jumpGaps.length - 1, Math.floor(p * jumpGaps.length))].toFixed(1);
  console.log(`seconds between jumps (target ~${player.settings.jumpEvery}s): p10 ${g(0.1)} p50 ${g(0.5)} p90 ${g(0.9)}`);
}
console.log("time per song:");
for (const [id, s] of [...seconds.entries()].sort((a, b) => b[1] - a[1])) {
  console.log(`  ${(s / 60).toFixed(1).padStart(6)} min  ${title(id)}`);
}
const unvisited = metas.filter((m) => !seconds.has(m.id)).map((m) => title(m.id));
console.log(`never visited: ${unvisited.length ? unvisited.join(", ") : "none"}`);
const uniq = [...new Set(problems)];
console.log(`problems: ${uniq.length}`);
for (const p of uniq.slice(0, 15)) console.log("  ", p);
process.exit(uniq.length ? 1 : 0);
