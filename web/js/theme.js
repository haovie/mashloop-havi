// Colour. Every song gets a colour from its mood (energy and valence, the two
// axes of Thayer's mood plane, measured by the analysis), and the interface
// takes the colour of the song that is playing: as the chain moves from song
// to song the whole room changes light, and during a mix it passes from one
// colour to the next. Two fixed presets sit beside it: Neon and the original
// amber.
//
// Colours are kept as OKLCH ({l, c, h}) so they mix evenly; the canvas gets
// sRGB strings, the page gets the three numbers as custom properties that CSS
// animates.

const AMBER = { l: 0.83, c: 0.13, h: 72 };

export const THEMES = {
  mood: {
    label: "Theo mood",
    ground: (a) => ({ h: a.h, c: 0.016 }),
    beatSpread: 80, beatL: [0.58, 0.84], beatC: [0.55, 1.25],
    glow: 1,
  },
  neon: {
    label: "Neon",
    ground: () => ({ h: 278, c: 0.03 }),
    beatSpread: 90, beatL: [0.56, 0.8], beatC: [1.0, 1.5],
    glow: 1.45,
  },
  amber: {
    label: "Hổ phách",
    ground: () => ({ h: 60, c: 0.009 }),
    beatSpread: null, // the analysis' own timbre colours, as in the first design
    glow: 1,
  },
};

let current = "mood";
let moods = new Map(); // id -> {energy, valence} spread over the library
let version = 0; // bumps whenever any song's colour may have changed
const cache = new Map();

export const themeName = () => current;
export const paletteVersion = () => `${current}.${version}`;

export function setTheme(name) {
  if (!THEMES[name] || name === current) return false;
  current = name;
  version++;
  cache.clear();
  return true;
}

// Moods are spread over the library before they become colours: a library of
// dance songs would otherwise come out as one red. Each axis is mostly the
// song's rank in the library, partly its absolute value, so a lone ballad still
// reads calm next to a room of funk.
export function setMoods(metas) {
  const list = metas.filter((m) => m.mood && Number.isFinite(m.mood.energy) && Number.isFinite(m.mood.valence));
  const rank = (key) => {
    const sorted = list.map((m) => m.mood[key]).sort((a, b) => a - b);
    return (v) => {
      if (sorted.length < 3) return v;
      const lo = sorted.indexOf(v), hi = sorted.lastIndexOf(v);
      return ((lo + hi) / 2) / (sorted.length - 1);
    };
  };
  const re = rank("energy"), rv = rank("valence");
  const next = new Map();
  for (const m of list) {
    next.set(m.id, {
      energy: 0.35 * m.mood.energy + 0.65 * re(m.mood.energy),
      valence: 0.35 * m.mood.valence + 0.65 * rv(m.mood.valence),
    });
  }
  const key = (mp) => [...mp].map(([id, v]) => `${id}:${v.energy.toFixed(3)}:${v.valence.toFixed(3)}`).sort().join("|");
  if (key(next) === key(moods)) return false;
  moods = next;
  version++;
  cache.clear();
  return true;
}

function hashHue(id) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return h % 360;
}

// Around the mood plane, angle measured from "pleasant" toward "energetic":
// content green, joyful amber, excited orange, tense red, gloomy violet, sad
// blue, calm teal. The hue only ever decreases along the way, so neighbouring
// moods always get neighbouring colours.
const WHEEL = [[0, 120], [45, 70], [90, 30], [135, -10], [180, -60], [225, -95], [270, -140], [315, -190], [360, -240]];

function wheel(deg) {
  for (let i = 1; i < WHEEL.length; i++) {
    if (deg <= WHEEL[i][0]) {
      const [a0, h0] = WHEEL[i - 1], [a1, h1] = WHEEL[i];
      return (((h0 + ((deg - a0) / (a1 - a0)) * (h1 - h0)) % 360) + 360) % 360;
    }
  }
  return WHEEL[0][1];
}

export function moodColor(id) {
  const m = moods.get(id);
  if (!m) return { l: 0.78, c: 0.11, h: hashHue(id) };
  const dx = m.valence - 0.5, dy = m.energy - 0.5;
  const deg = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360;
  const dist = Math.min(1, Math.hypot(dx, dy) / 0.5);
  return { l: 0.79, c: 0.085 + 0.075 * dist, h: wheel(deg) };
}

// The colour a song stands for wherever it appears: its loop, the dots that
// lead to it, its link in the setlist.
export function songColor(id) {
  const k = `s:${id}`;
  if (cache.has(k)) return cache.get(k);
  let col;
  if (current === "amber") col = { l: 0.72, c: 0.13, h: hashHue(id) };
  else if (current === "neon") col = { ...moodColor(id), l: 0.74, c: 0.24 };
  else col = moodColor(id);
  col = fit(col);
  cache.set(k, col);
  return col;
}

// The colour the interface takes while this song plays.
export function accentOf(id) {
  if (!id) return idleAccent();
  return current === "amber" ? AMBER : songColor(id);
}

export function idleAccent() {
  if (current === "neon") return fit({ l: 0.74, c: 0.24, h: 350 });
  return current === "mood" ? { l: 0.8, c: 0.1, h: 40 } : AMBER;
}

// A song's beats keep the timbre colouring of the original Infinite Jukebox
// (beats that sound alike look alike), folded into the song's own range of
// hues instead of the whole rainbow: each loop is one colour world.
export function beatColors(id, hexes) {
  const th = THEMES[current];
  if (th.beatSpread == null) return hexes;
  const k = `b:${id}:${hexes.length}`;
  if (cache.has(k)) return cache.get(k);
  const base = songColor(id);
  const out = hexes.map((hex) => {
    const n = parseInt(hex.slice(1), 16);
    const u = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.max(0, Math.min(1, (v / 255 - 0.25) / 0.75)));
    return css({
      l: th.beatL[0] + (th.beatL[1] - th.beatL[0]) * u[1],
      c: base.c * (th.beatC[0] + (th.beatC[1] - th.beatC[0]) * u[2]),
      h: base.h + (u[0] - 0.5) * th.beatSpread,
    });
  });
  cache.set(k, out);
  return out;
}

// Hairlines and small text on the canvas: near white, warmed or cooled by the song.
export function inkOf(id) {
  if (current === "amber") return { l: 0.93, c: 0.03, h: 75 };
  return { l: 0.94, c: 0.022, h: accentOf(id).h };
}

// Three colours that show what a theme will look like with this library.
export function preview(name, ids) {
  if (name === "amber") return [AMBER, { l: 0.72, c: 0.12, h: 52 }, { l: 0.6, c: 0.09, h: 40 }];
  const hues = moods.size ? null : [20, 170, 290];
  return [0, 1, 2].map((k) => {
    const base = hues ? { l: 0.79, c: 0.14, h: hues[k] } : moodColor(ids[k] ?? ids[0]);
    return fit(name === "neon" ? { ...base, l: 0.74, c: 0.24 } : base);
  });
}

// Three songs of the library whose colours lie furthest apart, for the previews.
export function spreadIds() {
  const list = [...moods.keys()].map((id) => [id, moodColor(id).h]).sort((a, b) => a[1] - b[1]);
  if (!list.length) return [];
  return [0, 1, 2].map((k) => list[Math.floor((k * list.length) / 3)][0]);
}

export const glowGain = () => THEMES[current].glow;
export const ground = (accent) => THEMES[current].ground(accent);

// ------------------------------------------------------------------ colour

// Mix in OKLCH along the short way round the hue circle.
export function mix(a, b, k) {
  if (!b || k <= 0) return a;
  if (k >= 1) return b;
  const dh = ((b.h - a.h + 540) % 360) - 180;
  return { l: a.l + (b.l - a.l) * k, c: a.c + (b.c - a.c) * k, h: (a.h + dh * k + 360) % 360 };
}

// Frame-rate independent easing of a colour toward a target.
export function ease(cur, target, k) {
  if (!cur) return { ...target };
  return mix(cur, target, k);
}

function linear(l, c, h) {
  const hr = (h * Math.PI) / 180;
  const a = c * Math.cos(hr), b = c * Math.sin(hr);
  const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s_ = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
    -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
    -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
  ];
}

const inGamut = (rgb) => rgb.every((v) => v >= -0.001 && v <= 1.001);

// Bring a colour inside sRGB by lowering its chroma only, so the hue and the
// lightness the mood asked for survive.
export function fit(col) {
  if (inGamut(linear(col.l, col.c, col.h))) return col;
  let lo = 0, hi = col.c;
  for (let i = 0; i < 14; i++) {
    const mid = (lo + hi) / 2;
    if (inGamut(linear(col.l, mid, col.h))) lo = mid;
    else hi = mid;
  }
  return { ...col, c: lo };
}

const enc = (x) => {
  x = Math.max(0, Math.min(1, x));
  return Math.round(255 * (x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055));
};

export function rgb(col) {
  const f = fit(col);
  return linear(f.l, f.c, f.h).map(enc);
}

export function css(col, alpha = 1) {
  const [r, g, b] = rgb(col);
  return alpha >= 1 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${Math.max(0, alpha).toFixed(3)})`;
}

// Lighter, a touch less saturated: highlights, the route thread, the playhead glow.
export const lift = (col, dl = 0.08) => ({ l: Math.min(0.97, col.l + dl), c: col.c * 0.8, h: col.h });

// ------------------------------------------------------------------- page

// The three numbers of the accent, the next song's colour and the ground go to
// the page as registered custom properties, so CSS animates every colour that
// is built from them (the chosen tabs, the play button, sliders, glows).
let lastCss = "";
export function applyPage(accent, second, fillAlpha) {
  const g = ground(accent);
  const s = second || accent;
  const vals = {
    "--acc-l": accent.l.toFixed(3), "--acc-c": accent.c.toFixed(3), "--acc-h": unwrap("--acc-h", accent.h),
    "--acc2-l": s.l.toFixed(3), "--acc2-c": s.c.toFixed(3), "--acc2-h": unwrap("--acc2-h", s.h),
    "--gh": unwrap("--gh", g.h), "--gc": g.c.toFixed(3),
    "--fill-a": fillAlpha.toFixed(3),
  };
  const key = Object.values(vals).join(",");
  if (key === lastCss) return;
  lastCss = key;
  const root = document.documentElement.style;
  for (const [k, v] of Object.entries(vals)) root.setProperty(k, v);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = css({ l: 0.17, c: g.c, h: g.h });
}

// CSS animates the hue as a plain number: keep each new value within half a
// turn of the last one so a change from 350 to 10 goes through 0, not 180.
const lastHue = new Map();
function unwrap(name, h) {
  const prev = lastHue.get(name);
  let v = h;
  if (prev !== undefined) v = prev + (((h - prev + 540) % 360) - 180);
  lastHue.set(name, v);
  return v.toFixed(1);
}
