/*
 * <jig-avatar> â€” animated neon mascot for Jig.
 * Dependency-free custom element drawn on a single Canvas 2D context.
 * All instances share one requestAnimationFrame ticker, unless clock="manual" (then call step() yourself).
 */

const TAU = Math.PI * 2;
const DEG = Math.PI / 180;
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const lerp = (a, b, t) => a + (b - a) * t;
const ease = (t) => t * t * (3 - 2 * t);
const frac = (v) => v - Math.floor(v);
const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${clamp(a, 0, 1)})`;
const mix = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];

function hash1(n) {
  const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
}
function noise1(x) {
  const i = Math.floor(x);
  return lerp(hash1(i), hash1(i + 1), ease(x - i)) * 2 - 1;
}
/* layered value noise: smooth, irregular, never visibly periodic */
const fbm = (x) => noise1(x) * 0.6 + noise1(x * 2.13 + 7.1) * 0.3 + noise1(x * 4.37 + 3.3) * 0.1;

/* damped spring: movements overshoot a little and settle instead of stopping dead */
class Spring {
  constructor(x = 0, k = 80, d = 10) {
    this.x = x;
    this.v = 0;
    this.k = k;
    this.d = d;
  }
  step(target, dt) {
    const n = dt > 1 / 50 ? 2 : 1;
    const h = dt / n;
    for (let i = 0; i < n; i++) {
      this.v += (this.k * (target - this.x) - this.d * this.v) * h;
      this.x += this.v * h;
    }
    return this.x;
  }
}

export const DEFAULT_TEMPO = 120;

/* Dance routines, in beats. A section's kind picks the move; `until` is the beat it ends on. */
const ROUTINES = {
  dance: [
    { until: 8, kind: 'step' },
    { until: 12, kind: 'wave' },
    { until: 14, kind: 'spin' },
    { until: 16, kind: 'tada' },
  ],
  flourish: [
    { until: 4, kind: 'step' },
    { until: 6, kind: 'spin' },
    { until: 8, kind: 'tada' },
  ],
  celebrate: [
    { until: 4, kind: 'wave' },
    { until: 6, kind: 'spin' },
    { until: 8, kind: 'tada' },
  ],
};
const routineLength = (r) => r[r.length - 1].until;

export const THEMES = ['dark', 'light'];

/* Light theme: the neon palette re-inked for cream paper. Bright colours keep their hue but drop to a deep,
   saturated lightness so they read as printed ink rather than light; near-whites become plum ink. */
const INK = [62, 30, 86];
const PAPER = [250, 242, 230];
function inkOf(c) {
  const r = c[0] / 255, g = c[1] / 255, b = c[2] / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const L = (max + min) / 2;
  const d = max - min;
  if (d < 0.14 && L > 0.72) return INK;
  if (L <= 0.42) return c;
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  const s = d === 0 ? 0 : Math.min(1, d / (1 - Math.abs(2 * L - 1)) * 1.05 + 0.08);
  const l = 0.33 + (L - 0.42) * 0.28;
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
  return [f(0) * 255, f(8) * 255, f(4) * 255];
}

const C = {
  magenta: [255, 64, 236],
  violet: [154, 84, 255],
  cyan: [64, 214, 255],
  amber: [255, 178, 70],
  orange: [255, 128, 36],
  blue: [86, 118, 255],
  pink: [255, 118, 206],
  white: [255, 246, 252],
  mint: [120, 255, 214],
};

/* World space is a 1000 Ã— 1000 square laid out to match the reference artwork. */
const HEAD = { x: 505, y: 318, rx: 162, ry: 150 };
const SHOULDER_L = { x: 452, y: 470 };
const SHOULDER_R = { x: 566, y: 478 };
const TAIL_TOP = 462;
const TAIL_TIP = { x: 498, y: 908 };
const CENTRE = { x: 508, y: 500 };
/* arms, hands and fingers keep at least this far outside the head (world units) so none of them hides behind it */
const ARM_MARGIN = 12;

export const STATES = ['idle', 'monitoring', 'thinking', 'working', 'talking', 'approval', 'success', 'error', 'paused', 'dance'];
export const TASKS = ['browsing', 'writing', 'coding', 'shopping', 'scheduling'];

const STATE_ALIASES = {
  resting: 'idle',
  sleeping: 'monitoring',
  background: 'monitoring',
  'needs-approval': 'approval',
  call: 'talking',
  'on-call': 'talking',
  blocked: 'error',
  dancing: 'dance',
  jig: 'dance',
};
const TASK_ALIASES = {
  research: 'browsing',
  researching: 'browsing',
  email: 'writing',
  code: 'coding',
  payments: 'shopping',
  payment: 'shopping',
  calendar: 'scheduling',
};

const BASE_POSE = {
  energy: 1, bobAmp: 9, bobFreq: 0.5, breath: 1, swirl: 0.07, sway: 26, swayFreq: 0.33, flare: 1, orbit: 0.04, wave: 14,
  eyeOpen: 1, lookX: 0, lookY: 0, happy: 0, sad: 0, smile: 1, mouthOpen: 0,
  tilt: -0.1, drop: 0,
  aL: 208, lL: 222, bL: -42, aR: 8, lR: 215, bR: 34, spreadL: 1, spreadR: 0.85, waveR: 0, typing: 0,
  cool: 0, amber: 0, desat: 0, timeScale: 1, dim: 0,
  /* expression and life: worried brows, ear droop (+) or perk (-), rhythmic groove */
  brow: 0, earDroop: 0, groove: 0, fidget: 1,
  wThink: 0, wWatch: 0, wTalk: 0, wApprove: 0, wSuccess: 0, wError: 0, wPause: 0, wDance: 0,
  wBrowse: 0, wWrite: 0, wCode: 0, wShop: 0, wCal: 0,
};
const POSE_KEYS = Object.keys(BASE_POSE);

const STATE_POSES = {
  idle: {},
  monitoring: {
    energy: 0.55, bobAmp: 4, bobFreq: 0.26, breath: 0.7, swirl: 0.03, sway: 14, swayFreq: 0.16, orbit: 0.016, wave: 8,
    eyeOpen: 0, smile: 0.7, tilt: -0.04, drop: 14, aL: 166, lL: 186, bL: -26, aR: 18, lR: 188, bR: 26,
    spreadL: 0.5, spreadR: 0.5, desat: 0.12, wWatch: 1, earDroop: 0.12, fidget: 0.4,
  },
  thinking: {
    energy: 1.1, bobFreq: 0.75, swirl: 0.42, sway: 36, swayFreq: 0.8, wave: 22, orbit: 0.22, lookX: 0.3, lookY: -1,
    smile: 0.55, tilt: 0.16, aL: 228, lL: 172, bL: -60, aR: 24, lR: 178, bR: 36, wThink: 1, earDroop: -0.06,
  },
  working: { energy: 1.05, swirl: 0.2, orbit: 0.1, sway: 30, swayFreq: 0.6, wave: 18, bobFreq: 0.7, groove: 1, fidget: 0.5 },
  talking: {
    energy: 1.05, swirl: 0.12, sway: 30, swayFreq: 0.5, smile: 1.05, aL: 200, aR: 12, spreadL: 1.1, wTalk: 1, groove: 0.4,
  },
  approval: {
    energy: 1.15, bobAmp: 14, bobFreq: 1.05, swirl: 0.14, eyeOpen: 1.08, smile: 1.15, tilt: 0.16,
    aL: 196, lL: 200, aR: -34, lR: 232, bR: 46, spreadR: 1.25, waveR: 1, amber: 1, wApprove: 1, earDroop: -0.14,
  },
  success: {
    energy: 1.25, bobAmp: 12, bobFreq: 1.2, swirl: 0.32, orbit: 0.2, sway: 40, swayFreq: 0.9, happy: 1, smile: 1.4,
    mouthOpen: 0.32, tilt: 0, aL: 224, lL: 232, aR: -44, lR: 232, spreadL: 1.3, spreadR: 1.3, wSuccess: 1,
    earDroop: -0.1,
  },
  /* error is worried and apologetic, never cross: soft frown, brows raised in the middle, ears drooping */
  error: {
    energy: 0.78, bobAmp: 3, bobFreq: 0.28, swirl: 0.025, sway: 10, swayFreq: 0.16, flare: 0.75, orbit: 0.012, wave: 7,
    eyeOpen: 1.02, lookY: 0.45, smile: -0.35, tilt: 0.2, drop: 34, brow: 1,
    aL: 132, lL: 186, bL: 32, aR: 52, lR: 186, bR: -32, spreadL: 0.6, spreadR: 0.6, cool: 0.6, wError: 1,
    earDroop: 0.42, fidget: 0.3,
  },
  /* paused is peacefully asleep */
  paused: {
    energy: 0.66, desat: 0.62, timeScale: 0.14, smile: 0.75, eyeOpen: 0, wPause: 1, earDroop: 0.26,
    fidget: 0, tilt: 0.06,
  },
  dance: {
    energy: 1.25, bobAmp: 4, bobFreq: 0.6, swirl: 0.28, orbit: 0.16, sway: 22, swayFreq: 0.5, wave: 18, smile: 1.35,
    mouthOpen: 0.15, tilt: 0, aL: 214, lL: 228, aR: -6, lR: 226, spreadL: 1.25, spreadR: 1.2,
    wDance: 1, fidget: 0,
  },
};

const TASK_POSES = {
  browsing: { wBrowse: 1, aL: 204, aR: -8, lookY: -0.1 },
  writing: { wWrite: 1, aR: -12, lR: 178, bR: 26, lookX: 0.85, lookY: 0.25, smile: 0.8 },
  coding: {
    wCode: 1, aL: 138, lL: 185, bL: 40, aR: 32, lR: 190, bR: -40, typing: 1, lookX: 0.6, lookY: 0.6,
    eyeOpen: 0.8, smile: 0.5, spreadL: 0.7, spreadR: 0.7,
  },
  shopping: { wShop: 1, aR: 6, lR: 200, lookX: 0.75, lookY: 0.3, smile: 1.2 },
  scheduling: { wCal: 1, aR: -8, lR: 236, lookX: 0.8, lookY: -0.05, smile: 0.8 },
};

/* Background work (for example read-only proactive research) keeps the state's animation but dims the
   rig, softens and slows the motion and half-closes the eyes. Applied on top of the state and task pose. */
const BACKGROUND_SCALE = {
  energy: 0.62, bobAmp: 0.45, sway: 0.5, swayFreq: 0.6, swirl: 0.45, orbit: 0.45, wave: 0.6, flare: 0.8,
  typing: 0.5, timeScale: 0.6, spreadL: 0.8, spreadR: 0.8,
};
const BACKGROUND_EYE_OPEN = 0.5;
const BACKGROUND_DESAT = 0.22;

function backgroundPose(pose) {
  const out = { ...pose, dim: 1 };
  for (const key in BACKGROUND_SCALE) out[key] = pose[key] * BACKGROUND_SCALE[key];
  out.eyeOpen = Math.min(pose.eyeOpen, BACKGROUND_EYE_OPEN);
  out.desat = Math.max(pose.desat, BACKGROUND_DESAT);
  return out;
}

const RIBBONS = [
  { o: -1.0, c: C.magenta, ph: 0.0, bw: 16 },
  { o: -0.62, c: C.violet, ph: 1.1, bw: 13 },
  { o: -0.42, c: C.cyan, ph: 2.9, bw: 8 },
  { o: -0.18, c: C.amber, ph: 2.3, bw: 15 },
  { o: 0.12, c: C.cyan, ph: 3.1, bw: 12 },
  { o: 0.32, c: C.orange, ph: 0.7, bw: 9 },
  { o: 0.6, c: C.blue, ph: 4.0, bw: 13 },
  { o: 0.98, c: C.magenta, ph: 5.2, bw: 15 },
];

const GLYPHS = ['{', '}', '<', '>', '/', ';', '=', '(', ')', '[', ']', '0', '1', '#', '*', '&'];

const CODE_LINES = [
  [0, [34, C.magenta], [52, C.cyan]],
  [1, [26, C.amber], [40, C.white]],
  [2, [44, C.cyan], [22, C.mint]],
  [2, [30, C.violet], [36, C.amber]],
  [1, [20, C.magenta]],
  [0, [14, C.cyan]],
];

/* Deterministic pseudo-random so every instance has identical, on-model composition. */
function seeded(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function buildShards() {
  const rnd = seeded(7);
  const shards = [];
  for (let i = 0; i < 10; i++) {
    shards.push({
      ang: (i / 10) * TAU + (rnd() - 0.5) * 0.4,
      rx: 395 + rnd() * 70,
      ry: 375 + rnd() * 60,
      size: 40 + rnd() * 40,
      rot: rnd() * TAU,
      rotSpeed: (rnd() - 0.5) * 0.5,
      tumble: rnd() * TAU,
      tumbleSpeed: 0.25 + rnd() * 0.45,
      kite: [
        [-0.08 + (rnd() - 0.5) * 0.2, -1],
        [0.5 + rnd() * 0.15, -0.1 + (rnd() - 0.5) * 0.2],
        [0.12 + (rnd() - 0.5) * 0.2, 1],
        [-0.5 - rnd() * 0.15, 0.12 + (rnd() - 0.5) * 0.2],
      ],
      bob: rnd() * TAU,
    });
  }
  return shards;
}

function buildSparks() {
  const rnd = seeded(11);
  const cols = [C.amber, C.magenta, C.cyan, C.violet, C.orange, C.pink, C.blue];
  const sparks = [];
  for (let i = 0; i < 46; i++) {
    sparks.push({
      ang: rnd() * TAU,
      r: 170 + rnd() * 320,
      speed: (0.4 + rnd() * 0.8) * (rnd() < 0.5 ? 1 : -1),
      size: 3 + rnd() * 6,
      c: cols[i % cols.length],
      tw: rnd() * TAU,
      twSpeed: 1 + rnd() * 2.5,
      square: rnd() < 0.55,
      spin: rnd() * TAU,
      glyph: GLYPHS[i % GLYPHS.length],
      lane: rnd(),
    });
  }
  return sparks;
}

function smoothPath(pts) {
  const p = new Path2D();
  const n = pts.length;
  p.moveTo(pts[0], pts[1]);
  for (let i = 2; i < n - 2; i += 2) {
    p.quadraticCurveTo(pts[i], pts[i + 1], (pts[i] + pts[i + 2]) / 2, (pts[i + 1] + pts[i + 3]) / 2);
  }
  p.lineTo(pts[n - 2], pts[n - 1]);
  return p;
}

function quadPoint(q, u, v) {
  const s = (u + 1) / 2;
  const t = (v + 1) / 2;
  const tx = lerp(q[0][0], q[1][0], s);
  const ty = lerp(q[0][1], q[1][1], s);
  const bx = lerp(q[3][0], q[2][0], s);
  const by = lerp(q[3][1], q[2][1], s);
  return [lerp(tx, bx, t), lerp(ty, by, t)];
}

function quadPath(q) {
  const p = new Path2D();
  p.moveTo(q[0][0], q[0][1]);
  for (let i = 1; i < 4; i++) p.lineTo(q[i][0], q[i][1]);
  p.closePath();
  return p;
}

/* ---------- shared ticker ---------- */

const instances = new Set();
let rafId = 0;
let lastFrame = 0;

function frame(now) {
  const dt = lastFrame ? Math.min(0.05, (now - lastFrame) / 1000) : 1 / 60;
  lastFrame = now;
  /* schedule first so an exception in one instance surfaces without freezing the others */
  rafId = instances.size ? requestAnimationFrame(frame) : 0;
  if (!rafId) lastFrame = 0;
  for (const el of instances) {
    try {
      el._tick(dt);
    } catch (err) {
      reportError(err);
    }
  }
}

function track(el) {
  instances.add(el);
  if (!rafId) rafId = requestAnimationFrame(frame);
}

function untrack(el) {
  instances.delete(el);
}

const reducedQuery = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;

const TEMPLATE = `
<style>
  :host { display: inline-block; position: relative; width: 240px; height: 240px; contain: strict; }
  canvas { display: block; width: 100%; height: 100%; }
</style>
<canvas part="canvas"></canvas>`;

export class JigAvatar extends HTMLElement {
  static get observedAttributes() {
    return [
      'state', 'task', 'background', 'audio-level', 'shape', 'framing', 'reduced-motion', 'theme',
      'seed', 'clock', 'tempo', 'look-at', 'user-typing',
    ];
  }

  constructor() {
    super();
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = TEMPLATE;
    this._canvas = root.querySelector('canvas');
    this._ctx = this._canvas.getContext('2d');
    this._state = 'idle';
    this._task = null;
    this._background = false;
    this._target = { ...BASE_POSE };
    this._cur = { ...BASE_POSE };
    this._audio = 0;
    this._audioS = 0;
    this._audioSlow = 0;
    this._t = 0;
    this._rt = 0;
    this._ph = { bob: 0, breath: 0, swirl: 0, sway: 0, orbit: 0, wave: 0, pulse: 0, type: 0, groove: 0 };
    this._reseed((Math.random() * 4294967296) >>> 0);
    this._sp = {
      aL: new Spring(BASE_POSE.aL, 110, 12), aR: new Spring(BASE_POSE.aR, 110, 12),
      earL: new Spring(0, 95, 6.5), earR: new Spring(0, 95, 6.5),
      tilt: new Spring(0, 60, 7), hop: new Spring(0, 120, 9), sq: new Spring(0, 170, 9),
      foot: new Spring(0, 120, 13), lag: new Spring(0, 34, 6.5), headX: new Spring(0, 70, 11),
      gazeX: new Spring(0, 300, 26), gazeY: new Spring(0, 300, 26),
      fingerL: new Spring(0, 70, 6), fingerR: new Spring(0, 70, 6),
    };
    this._prevHeadY = 0;
    this._prevVy = 0;
    this._blinkStart = -10;
    this._doubleBlink = false;
    this._gaze = { x: 0, y: 0, next: 0, microX: 0, microY: 0, microNext: 0 };
    this._pointer = { x: 0, y: 0, at: -100 };
    this._lookAt = null;
    this._lookAtMissing = false;
    this._wiggle = 0;
    this._fl = null; /* the one-off dance flourish in progress, if any */
    this._danceBeat = 0;
    this._lastBeat = -1;
    this._splash = 0;
    this._onsetCool = 0;
    this._idleFor = 0;
    this._burst = [];
    this._browseLook = 0;
    this._shards = buildShards();
    this._sparks = buildSparks();
    this._cssW = 0;
    this._cssH = 0;
    this._dpr = 1;
    this._visible = true;
    this._reflecting = false;
    this._onReduced = () => {};
    this._lightK = 0; /* 0 = dark rendering, 1 = light; eased towards the theme so switches cross-fade */
    this._lightSettled = false;
    this._light = false;
    this._onHead = false;
    this._addOp = 'lighter';
    this._bufs = [];
  }

  connectedCallback() {
    this._ro = new ResizeObserver((entries) => {
      const box = entries[0].contentRect;
      this._resize(box.width, box.height);
    });
    this._ro.observe(this);
    this._io = new IntersectionObserver((entries) => {
      this._visible = entries[entries.length - 1].isIntersecting;
    });
    this._io.observe(this);
    this._onPointer = (e) => {
      const r = this.getBoundingClientRect();
      if (!r.width || !r.height) return;
      this._pointer.x = (e.clientX - r.left) / r.width;
      this._pointer.y = (e.clientY - r.top) / r.height;
      this._pointer.at = this._rt;
    };
    this._onLeave = () => {
      this._pointer.at = -100;
    };
    this.addEventListener('pointermove', this._onPointer);
    this.addEventListener('pointerleave', this._onLeave);
    if (!this.hasAttribute('role')) this.setAttribute('role', 'img');
    this._updateLabel();
    if (!this._lightSettled) {
      this._lightK = this.theme === 'light' ? 1 : 0; /* the first frame starts in the right theme, no fade */
      this._lightSettled = true;
    }
    if (!this.manualClock) track(this);
  }

  disconnectedCallback() {
    this._ro.disconnect();
    this._io.disconnect();
    this.removeEventListener('pointermove', this._onPointer);
    this.removeEventListener('pointerleave', this._onLeave);
    untrack(this);
  }

  attributeChangedCallback(name, oldValue, value) {
    if (this._reflecting || oldValue === value) return;
    if (name === 'seed') {
      if (value === null) return;
      const n = Number(value);
      if (!Number.isInteger(n) || n < 0) throw new RangeError(`jig-avatar: seed must be a whole number of 0 or more, received "${value}"`);
      this._reseed(n);
      return;
    }
    if (name === 'clock') {
      if (value !== null && !['auto', 'manual'].includes(value)) {
        throw new RangeError(`jig-avatar: unknown clock "${value}". Expected auto or manual`);
      }
      if (this.isConnected) {
        if (this.manualClock) untrack(this);
        else track(this);
      }
      return;
    }
    if (name === 'tempo') {
      if (value !== null) this._parseTempo(value);
      return;
    }
    if (name === 'look-at') {
      this._lookAt = value === null || value === '' ? null : this._parseLookAt(value);
      this._lookAtMissing = false;
      return;
    }
    if (name === 'user-typing') {
      /* stop a flourish politely at the next beat rather than mid-step */
      if (this._userTyping && this._fl && !this._fl.held) this._fl.len = Math.min(this._fl.len, Math.ceil(this._fl.beat) + 1);
      return;
    }
    if (name === 'state' || name === 'task' || name === 'background') {
      /* state, task and background may arrive as separate attribute writes, so apply them together once settled */
      if (this._pendingAttr) return;
      this._pendingAttr = true;
      queueMicrotask(() => {
        this._pendingAttr = false;
        const state = this.getAttribute('state') || 'idle';
        const canonical = STATE_ALIASES[state] || state;
        const background = this.hasAttribute('background') && this.getAttribute('background') !== 'false';
        this.setState(state, canonical === 'working' ? { task: this.getAttribute('task'), background } : { background });
      });
    } else if (name === 'audio-level') {
      if (value !== null) this.setAudioLevel(Number(value));
    } else if (name === 'shape') {
      if (value !== null && !['rounded', 'circle', 'none'].includes(value)) {
        throw new RangeError(`jig-avatar: unknown shape "${value}". Expected rounded, circle or none`);
      }
    } else if (name === 'framing') {
      if (value !== null && !['auto', 'icon', 'full'].includes(value)) {
        throw new RangeError(`jig-avatar: unknown framing "${value}". Expected auto, icon or full`);
      }
    } else if (name === 'theme') {
      if (value !== null && !THEMES.includes(value)) {
        throw new RangeError(`jig-avatar: unknown theme "${value}". Expected ${THEMES.join(' or ')}`);
      }
    }
  }

  /** 'dark' (default: neon on navy-black) or 'light' (deep inks on a transparent background, for cream pages). */
  get theme() {
    const value = this.getAttribute('theme');
    return value === 'light' ? 'light' : 'dark';
  }

  set theme(value) {
    if (!THEMES.includes(value)) {
      throw new RangeError(`jig-avatar: unknown theme "${value}". Expected ${THEMES.join(' or ')}`);
    }
    this.setAttribute('theme', value);
  }

  get state() {
    return this._state;
  }

  get task() {
    return this._task;
  }

  get background() {
    return this._background;
  }

  get audioLevel() {
    return this._audio;
  }

  /** True when clock="manual": the shared ticker leaves this avatar alone and step() drives it. */
  get manualClock() {
    return this.getAttribute('clock') === 'manual';
  }

  /** Seconds of animation time this avatar has run (render time, independent of state slow-downs). */
  get time() {
    return this._rt;
  }

  /** Dance tempo in beats per minute (tempo attribute, default 120). */
  get tempo() {
    const value = this.getAttribute('tempo');
    return value === null ? DEFAULT_TEMPO : this._parseTempo(value);
  }

  /** True while the dance state or a flourish (dance() or the success celebration) is moving Jig. */
  get dancing() {
    return !!this._fl || (this._state === 'dance' && !this._reduced);
  }

  get _userTyping() {
    return this.hasAttribute('user-typing') && this.getAttribute('user-typing') !== 'false';
  }

  _parseTempo(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n < 40 || n > 220) {
      throw new RangeError(`jig-avatar: tempo must be between 40 and 220 beats per minute, received "${value}"`);
    }
    return n;
  }

  /* look-at is either "x,y" in fractions of the avatar's own box (so "0.5,1.6" is below it) or a CSS selector */
  _parseLookAt(value) {
    const parts = value.split(',');
    if (parts.length === 2 && parts.every((p) => p.trim() !== '' && Number.isFinite(Number(p)))) {
      return { x: Number(parts[0]), y: Number(parts[1]) };
    }
    try {
      document.querySelector(value);
    } catch (err) {
      throw new RangeError(`jig-avatar: look-at must be "x,y" or a CSS selector, received "${value}" (${err.message})`);
    }
    return { selector: value };
  }

  _reseed(seed) {
    this._seed = seed;
    this._rng = seeded(seed ^ 0x9e3779b9);
    const rnd = this._rng;
    this._nextBlink = 1.2 + rnd() * 2.5;
    this._nextFidget = 2.5 + rnd() * 4;
    this._nextFlourish = 60 + rnd() * 60;
  }

  /**
   * Advance the animation by dt seconds and draw one frame. For clock="manual" (frame-by-frame video
   * rendering): with a fixed seed and fixed steps the output is identical on every run.
   */
  step(dt = 1 / 60) {
    if (typeof dt !== 'number' || !(dt > 0) || dt > 1) {
      throw new RangeError(`jig-avatar: step() takes a time step between 0 and 1 second, received ${dt}`);
    }
    if (!this.manualClock) throw new Error('jig-avatar: step() needs clock="manual"; the shared ticker drives this avatar');
    if (!this._cssW || !this._cssH) {
      const r = this.getBoundingClientRect();
      if (!r.width || !r.height) throw new Error('jig-avatar: cannot step an avatar with no size (is it in the document and visible?)');
      this._resize(r.width, r.height);
    }
    this._tick(dt, true);
  }

  /** Step through `seconds` in fixed 1/fps increments (manual clock only). */
  advance(seconds, fps = 60) {
    const n = Math.round(seconds * fps);
    for (let i = 0; i < n; i++) this.step(1 / fps);
  }

  /**
   * Play a short dance flourish over the current state: a few jig steps, a spin and a ta-da.
   * Resolves true when it finishes, false if it is cut short (a newer dance, or a state change that ends it),
   * and false straight away when reduced motion is on (no dancing then).
   * @param {{routine?: 'flourish'|'celebrate'|'dance'}} [options] 'dance' plays the full 16-beat routine once
   */
  dance(options = {}) {
    const routine = options.routine || 'flourish';
    if (!ROUTINES[routine]) {
      throw new RangeError(`jig-avatar: unknown dance routine "${routine}". Expected ${Object.keys(ROUTINES).join(', ')}`);
    }
    if (this._state === 'paused') throw new Error('jig-avatar: Jig is paused, so it cannot dance');
    if (this._reduced) return Promise.resolve(false);
    return new Promise((resolve) => {
      this._startFlourish(routine, resolve);
    });
  }

  _startFlourish(routine, resolve) {
    if (this._fl && this._fl.resolve) this._fl.resolve(false);
    this._fl = { routine: ROUTINES[routine], name: routine, beat: 0, len: routineLength(ROUTINES[routine]), resolve, lastBeat: -1 };
    this._sp.sq.v += 2.2;
  }

  /**
   * Switch animation state. Throws on unknown states or tasks rather than guessing.
   * @param {string} name one of STATES (or an alias such as 'sleeping', 'needs-approval', 'blocked')
   * @param {{task?: string, background?: boolean}} [options] task is required when name is 'working';
   *   background dims the state to show it is background work (for example read-only research)
   */
  setState(name, options = {}) {
    const state = STATE_ALIASES[name] || name;
    if (!STATES.includes(state)) {
      throw new RangeError(`jig-avatar: unknown state "${name}". Expected one of: ${STATES.join(', ')}`);
    }
    let task = null;
    if (state === 'working') {
      const raw = options.task;
      if (!raw) throw new TypeError('jig-avatar: the "working" state requires a task option');
      task = TASK_ALIASES[raw] || raw;
      if (!TASKS.includes(task)) {
        throw new RangeError(`jig-avatar: unknown task "${raw}". Expected one of: ${TASKS.join(', ')}`);
      }
    } else if (options.task) {
      throw new TypeError(`jig-avatar: the "${state}" state does not take a task`);
    }
    const background = options.background ?? false;
    if (typeof background !== 'boolean') {
      throw new TypeError(`jig-avatar: the background option must be true or false, received ${background}`);
    }

    const changed = state !== this._state || task !== this._task || background !== this._background;
    const previous = this._state;
    this._state = state;
    this._task = task;
    this._background = background;
    const pose = { ...BASE_POSE, ...STATE_POSES[state], ...(task ? TASK_POSES[task] : {}) };
    this._target = background ? backgroundPose(pose) : pose;

    if (changed && state !== previous) {
      /* squash and stretch on every change of state, a little hop, and often a blink */
      const m = this._reduced ? 0 : 1;
      this._sp.sq.v += (state === 'success' ? 5 : state === 'error' ? -2.5 : 3) * m;
      if (state !== 'paused' && state !== 'monitoring') this._sp.hop.v += (state === 'error' ? 0 : 60) * m;
      this._sp.earL.v += 2.5 * m;
      this._sp.earR.v += 2.5 * m;
      if (this._rng() < 0.6) this._blinkStart = this._rt;
      if (state === 'dance') {
        this._danceBeat = 0;
        this._lastBeat = -1;
      }
      if (this._fl && state !== 'success' && this._fl.name === 'celebrate') this._endFlourish();
      if (this._fl && (state === 'paused' || state === 'dance')) this._endFlourish();
    }
    if (changed && state === 'success' && previous !== 'success') {
      this._spawnBurst();
      if (!this._reduced) this._startFlourish('celebrate', null);
    }

    this._reflecting = true;
    this.setAttribute('state', state);
    if (task) this.setAttribute('task', task);
    else this.removeAttribute('task');
    if (background) this.setAttribute('background', '');
    else this.removeAttribute('background');
    this._reflecting = false;
    this._updateLabel();

    if (changed) {
      this.dispatchEvent(new CustomEvent('jig-statechange', { detail: { state, task, previous, background }, bubbles: true }));
    }
  }

  /** Feed the live voice level (0..1) used by the talking state. */
  setAudioLevel(level) {
    if (typeof level !== 'number' || Number.isNaN(level) || level < 0 || level > 1) {
      throw new RangeError(`jig-avatar: audio level must be a number between 0 and 1, received ${level}`);
    }
    this._audio = level;
  }

  _endFlourish(finished = false) {
    const fl = this._fl;
    this._fl = null;
    if (fl && fl.resolve) fl.resolve(finished);
  }

  _updateLabel() {
    const name = this._state === 'dance' ? 'dancing' : this._state;
    const label = this._task ? `Jig is ${name}: ${this._task}` : `Jig is ${name}`;
    this.setAttribute('aria-label', this._background ? `${label}, in the background` : label);
  }

  _resize(w, h) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    if (w === this._cssW && h === this._cssH && dpr === this._dpr) return;
    this._cssW = w;
    this._cssH = h;
    this._dpr = dpr;
    this._canvas.width = Math.max(1, Math.round(w * this._dpr));
    this._canvas.height = Math.max(1, Math.round(h * this._dpr));
    /* resizing clears the canvas; a manually clocked avatar would otherwise stay blank until its next step */
    if (this.manualClock && this._pose && w && h) this._render(this._reduced ? 0.15 : 1);
  }

  get _reduced() {
    if (this.hasAttribute('reduced-motion')) return this.getAttribute('reduced-motion') !== 'false';
    return !!(reducedQuery && reducedQuery.matches);
  }

  _spawnBurst() {
    if (this._reduced) return;
    const rnd = this._rng;
    const cols = [C.amber, C.magenta, C.cyan, C.violet, C.pink, C.white];
    for (let i = 0; i < 70; i++) {
      const a = rnd() * TAU;
      const sp = 260 + rnd() * 520;
      this._burst.push({
        x: HEAD.x, y: HEAD.y + 60,
        vx: Math.cos(a) * sp, vy: Math.sin(a) * sp - 260,
        life: 0, max: 1.4 + rnd() * 1.1,
        c: cols[i % cols.length], rot: rnd() * TAU, vr: (rnd() - 0.5) * 14, s: 5 + rnd() * 7,
      });
    }
  }

  /* ---------- per-frame update ---------- */

  _tick(dt, manual = false) {
    if (!manual && (!this._visible || !this._cssW || !this._cssH)) return;
    const reduced = this._reduced;
    const cur = this._cur;
    const tgt = this._target;
    const k = 1 - Math.exp(-dt * (reduced ? 7 : 3.6));
    for (const key of POSE_KEYS) cur[key] += (tgt[key] - cur[key]) * k;

    const m = reduced ? 0.15 : 1;
    const ts = cur.timeScale * (reduced ? 0.35 : 1);
    this._rt += dt;
    this._t += dt * ts;
    const t = this._t;
    const ph = this._ph;
    ph.bob += dt * ts * cur.bobFreq * TAU;
    /* breathing never stops, even when paused, and its rate wanders so it never looks like a loop */
    ph.breath += dt * (0.35 + 0.65 * ts) * TAU * 0.25 * (1 + 0.3 * fbm(this._rt * 0.17 + 4));
    ph.swirl += dt * ts * cur.swirl * TAU * m;
    ph.sway += dt * ts * cur.swayFreq * TAU;
    ph.orbit += dt * ts * cur.orbit * TAU * (reduced ? 0.3 : 1);
    ph.wave += dt * ts * 2.1 * TAU;
    ph.pulse += dt * ts * 0.9 * TAU;
    ph.type += dt * ts * 3.2 * TAU;
    ph.groove += dt * ts * 0.85 * Math.PI * (1 + 0.08 * fbm(this._rt * 0.1));

    const target = this._audio;
    const ak = 1 - Math.exp(-dt * (target > this._audioS ? 28 : 9));
    this._audioS += (target - this._audioS) * ak;
    this._audioSlow += (this._audioS - this._audioSlow) * (1 - Math.exp(-dt * 2.5));

    /* beats: the dance state keeps its own count from the moment it starts; flourishes count from zero */
    const bps = (this.tempo / 60) * (reduced ? 0 : 1);
    if (cur.wDance > 0.01 || this._state === 'dance') this._danceBeat += dt * bps;
    if (this._fl) {
      this._fl.beat += dt * bps * (this._state === 'paused' ? 0 : 1);
      if (this._fl.beat >= this._fl.len) this._endFlourish(true);
      else if (reduced) this._endFlourish(false);
    }
    this._scheduleFlourish(dt, reduced);
    if (!reduced) this._fidgets(t);
    this._updateBlink();
    this._updateGaze(dt);

    for (let i = this._burst.length - 1; i >= 0; i--) {
      const b = this._burst[i];
      b.life += dt;
      if (b.life >= b.max) {
        this._burst.splice(i, 1);
        continue;
      }
      b.vx *= 1 - dt * 1.4;
      b.vy = b.vy * (1 - dt * 1.4) + 420 * dt;
      b.x += b.vx * dt;
      b.y += b.vy * dt;
      b.rot += b.vr * dt;
    }

    const lightTarget = this.theme === 'light' ? 1 : 0;
    if (this._lightK !== lightTarget) {
      const step = dt / (reduced ? 0.15 : 0.6);
      this._lightK = lightTarget > this._lightK ? Math.min(lightTarget, this._lightK + step) : Math.max(lightTarget, this._lightK - step);
    }

    this._pose = this._computePose(m, dt);
    this._render(m);
  }

  _scheduleFlourish(dt, reduced) {
    /* idle now and then breaks into a short jig: rarely (a minute or two apart) and never while the user types */
    if (this._state !== 'idle' || this._background || reduced || this._fl) {
      if (this._state !== 'idle') this._idleFor = 0;
      return;
    }
    this._idleFor += dt;
    if (this._idleFor < this._nextFlourish) return;
    if (this._userTyping) {
      this._nextFlourish = this._idleFor + 8;
      return;
    }
    this._idleFor = 0;
    this._nextFlourish = 60 + this._rng() * 75;
    this._startFlourish('flourish', null);
  }

  _fidgets(t) {
    const cur = this._cur;
    if (t < this._nextFidget) return;
    const rnd = this._rng;
    const f = cur.fidget * (this._fl || cur.wDance > 0.3 ? 0 : 1);
    this._nextFidget = t + 3.5 + rnd() * 6;
    if (f < 0.05) return;
    const pick = rnd();
    const sp = this._sp;
    if (pick < 0.24) {
      (rnd() < 0.5 ? sp.earL : sp.earR).v += (rnd() < 0.5 ? 7 : -5) * f;
    } else if (pick < 0.42) {
      sp.tilt.v += (rnd() - 0.5) * 2.2 * f;
    } else if (pick < 0.58) {
      this._gaze.next = 0; /* a fresh glance somewhere */
    } else if (pick < 0.74) {
      this._wiggle = 1;
    } else if (pick < 0.88) {
      sp.aL.v += (rnd() < 0.5 ? 120 : -90) * f;
      sp.aR.v += (rnd() < 0.5 ? -120 : 90) * f;
      sp.sq.v += 1.2 * f;
    } else if (this._state === 'idle') {
      sp.hop.v += 150 * f;
      sp.sq.v += 2 * f;
      sp.earL.v += 3;
      sp.earR.v += 3;
    }
  }

  _updateBlink() {
    const cur = this._cur;
    const rt = this._rt;
    if (rt >= this._nextBlink) {
      this._blinkStart = rt;
      const focus = cur.wThink + cur.wCode + cur.wBrowse * 0.5;
      this._nextBlink = rt + (this._doubleBlink ? 0.26 : 1.1 + Math.min(6, -Math.log(1 - this._rng()) * (2.6 + focus * 1.5)));
      this._doubleBlink = !this._doubleBlink && this._rng() < 0.18;
    }
    const e = rt - this._blinkStart;
    if (e < 0.06) this._blink = ease(e / 0.06);
    else if (e < 0.1) this._blink = 1;
    else if (e < 0.24) this._blink = 1 - ease((e - 0.1) / 0.14);
    else this._blink = 0;
  }

  /* where Jig wants to look, in the -1..1 eye range: the pointer, a look-at target, the task, or a wander */
  _updateGaze(dt) {
    const cur = this._cur;
    const g = this._gaze;
    const rt = this._rt;
    const rnd = this._rng;
    const focus = clamp(cur.wThink + cur.wBrowse + cur.wWrite + cur.wCode + cur.wShop + cur.wCal + cur.wError * 0.7, 0, 1);
    let free = null;
    const pointerActive = !this.manualClock && rt - this._pointer.at < 2.5;
    if (pointerActive) free = this._boxToLook(this._pointer.x, this._pointer.y);
    else if (this._lookAt) free = this._lookAtPoint();
    if (!free) {
      if (rt > g.next) {
        g.next = rt + 0.7 + rnd() * 2.6;
        /* while dancing Jig mostly looks out at its audience */
        const showing = this._fl || cur.wDance > 0.5 ? 0.45 : 0;
        if (rnd() < 0.42 + showing) {
          g.x = (rnd() - 0.5) * 0.25;
          g.y = (rnd() - 0.5) * 0.2;
        } else {
          g.x = (rnd() - 0.5) * 1.5;
          g.y = (rnd() - 0.6) * 1.0;
        }
      }
      free = g;
    }
    if (rt > g.microNext) {
      g.microNext = rt + 0.25 + rnd() * 0.7;
      g.microX = (rnd() - 0.5) * 0.12;
      g.microY = (rnd() - 0.5) * 0.1;
    }
    const pointerK = pointerActive && free !== g ? 0.55 : 0;
    const k = clamp(1 - focus + focus * pointerK, 0, 1);
    const wx = lerp(cur.lookX, free.x, k) + g.microX;
    const wy = lerp(cur.lookY, free.y, k) + g.microY;
    const sx = this._sp.gazeX;
    if (Math.abs(wx - sx.x) > 0.6 && rnd() < 0.3 && rt - this._blinkStart > 0.6) this._blinkStart = rt;
    sx.step(clamp(wx, -1, 1), dt);
    this._sp.gazeY.step(clamp(wy, -1, 1), dt);
  }

  /* a point in fractions of the element box, as a -1..1 look direction from Jig's head */
  _boxToLook(fx, fy) {
    const view = this._view;
    if (!view || !this._scale) return null;
    const wx = view.x + (fx * this._cssW - this._cssW / 2) / this._scale;
    const wy = view.y + (fy * this._cssH - this._cssH / 2) / this._scale;
    return { x: clamp((wx - HEAD.x) / 260, -1, 1), y: clamp((wy - HEAD.y - 30) / 240, -1, 1) };
  }

  _lookAtPoint() {
    const la = this._lookAt;
    if (!la.selector) return this._boxToLook(la.x, la.y);
    const target = document.querySelector(la.selector);
    if (!target) {
      if (!this._lookAtMissing) {
        this._lookAtMissing = true;
        reportError(new Error(`jig-avatar: look-at "${la.selector}" matches no element, so Jig is not looking at it`));
      }
      return null;
    }
    this._lookAtMissing = false;
    const me = this.getBoundingClientRect();
    const r = target.getBoundingClientRect();
    if (!me.width || !me.height) return null;
    return this._boxToLook((r.left + r.width / 2 - me.left) / me.width, (r.top + r.height / 2 - me.top) / me.height);
  }

  /* ---------- rendering ---------- */

  /* Each theme is a complete pass; between themes both passes are drawn and cross-faded. */
  _render(m) {
    const k = ease(this._lightK);
    if (k <= 0 || k >= 1) {
      this._light = k >= 1;
      this._paint(m);
      return;
    }
    const main = this._ctx;
    const W = this._canvas.width;
    const H = this._canvas.height;
    const bufs = [0, 1].map((i) => {
      let b = this._bufs[i];
      if (!b) {
        b = document.createElement('canvas').getContext('2d');
        this._bufs[i] = b;
      }
      if (b.canvas.width !== W || b.canvas.height !== H) {
        b.canvas.width = W;
        b.canvas.height = H;
      }
      return b;
    });
    try {
      this._light = false;
      this._ctx = bufs[0];
      this._paint(m);
      this._light = true;
      this._ctx = bufs[1];
      this._paint(m);
    } finally {
      this._ctx = main;
    }
    main.setTransform(1, 0, 0, 1, 0, 0);
    main.globalCompositeOperation = 'source-over';
    main.clearRect(0, 0, W, H);
    main.globalAlpha = 1 - k;
    main.drawImage(bufs[0].canvas, 0, 0);
    main.globalCompositeOperation = 'lighter';
    main.globalAlpha = k;
    main.drawImage(bufs[1].canvas, 0, 0);
    main.globalCompositeOperation = 'source-over';
    main.globalAlpha = 1;
  }

  _paint(m) {
    const ctx = this._ctx;
    this._addOp = this._light ? 'source-over' : 'lighter';
    const cur = this._cur;
    const W = this._canvas.width;
    const H = this._canvas.height;
    const css = Math.min(this._cssW, this._cssH);
    const framing = this.getAttribute('framing') || 'auto';
    const icon = framing === 'icon' || (framing === 'auto' && css < 110);
    const view = icon ? { x: 505, y: 405, s: 650 } : { x: 500, y: 500, s: 1000 };
    const scale = css / view.s;
    this._scale = scale;
    this._view = view;
    this._minW = (icon ? 0.75 : 0.9) / scale;
    this._icon = icon;
    this._spread = icon ? 0.74 : 1;
    this._alphaK = clamp(cur.energy, 0, 1.4);

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    ctx.clearRect(0, 0, W, H);

    const shape = this.getAttribute('shape') || 'rounded';
    ctx.save();
    ctx.beginPath();
    if (shape === 'circle') ctx.arc(W / 2, H / 2, Math.min(W, H) / 2, 0, TAU);
    else if (shape === 'rounded') ctx.roundRect(0, 0, W, H, Math.min(W, H) * 0.12);
    else ctx.rect(0, 0, W, H);
    ctx.clip();

    const d = this._dpr * scale;
    ctx.setTransform(d, 0, 0, d, this._dpr * (this._cssW / 2) - view.x * d, this._dpr * (this._cssH / 2) - view.y * d);

    if (this._debugLayer) {
      this._paintDebugLayer();
      ctx.restore();
      return;
    }
    if (shape !== 'none' && !this._light) this._drawBackground(view);

    ctx.globalCompositeOperation = this._addOp;
    this._drawVortex(view);
    this._drawAura();
    this._drawSonar();
    const shardFrames = this._computeShards(m);
    this._drawShards(shardFrames, false);
    this._drawTaskBack(shardFrames);
    this._drawSparks(m);

    this._drawBase();
    ctx.save();
    this._applyGroup();
    this._drawTail();
    this._drawArms();
    this._drawHead();
    ctx.restore();

    ctx.globalCompositeOperation = this._addOp;
    this._drawShards(shardFrames, true);
    this._drawTaskFront(shardFrames);
    this._drawBurst();
    this._drawOverlays();
    ctx.restore();
  }

  /* the whole character hops, sways and squashes about its ribbon foot; the pool stays on the ground */
  _applyGroup() {
    const ctx = this._ctx;
    const P = this._pose;
    const fx = TAIL_TIP.x + P.footX + P.groupX;
    ctx.translate(fx, TAIL_TIP.y);
    ctx.scale(P.sqX, P.sqY);
    ctx.translate(-fx + P.groupX, -TAIL_TIP.y - P.hop);
    if (P.spinX !== 1) {
      const cx = HEAD.x + P.headDX;
      ctx.translate(cx, 0);
      ctx.scale(P.spinX, 1);
      ctx.translate(-cx, 0);
    }
  }

  /* Flat masks of one layer of the current frame, for checking that arms never hide behind the head. */
  _paintDebugLayer() {
    const ctx = this._ctx;
    ctx.save();
    this._applyGroup();
    if (this._debugLayer === 'arms') {
      this._drawArms();
    } else {
      const h = this._pose.head;
      ctx.translate(h.x, h.y);
      ctx.rotate(h.tilt);
      ctx.scale(h.s, h.s);
      ctx.beginPath();
      ctx.ellipse(0, 0, HEAD.rx, HEAD.ry, 0, 0, TAU);
      ctx.fillStyle = 'rgb(255,0,0)';
      ctx.fill();
    }
    ctx.restore();
  }

  /**
   * Test hook: draws the current frame's arms (green) or head (red) as flat masks and returns the pixels,
   * then redraws the normal frame. Does not advance time.
   * @param {'arms'|'head'} layer
   */
  layerMask(layer) {
    if (layer !== 'arms' && layer !== 'head') throw new RangeError(`jig-avatar: unknown mask layer "${layer}". Expected arms or head`);
    if (!this._pose) throw new Error('jig-avatar: no frame drawn yet');
    const m = this._reduced ? 0.15 : 1;
    const light = this._light;
    this._debugLayer = layer;
    let data;
    try {
      this._paint(m);
      data = this._ctx.getImageData(0, 0, this._canvas.width, this._canvas.height).data;
    } finally {
      this._debugLayer = null;
      this._light = light;
      this._render(m);
    }
    return data;
  }

  _tone(c, amberMix = 0) {
    const p = this._cur;
    let r = c[0], g = c[1], b = c[2];
    /* tints follow each colour's own brightness so dark fills stay dark */
    if (amberMix > 0.001) {
      const L = Math.max(r, g, b);
      r = lerp(r, L, amberMix);
      g = lerp(g, L * 0.69, amberMix);
      b = lerp(b, L * 0.24, amberMix);
    }
    if (p.cool > 0.001) {
      const L = Math.max(r, g, b);
      const k = p.cool * 0.75;
      r = lerp(r, L * 0.24, k);
      g = lerp(g, L * 0.6, k);
      b = lerp(b, L, k);
    }
    if (p.desat > 0.001) {
      const y = r * 0.3 + g * 0.59 + b * 0.11;
      r = lerp(r, y, p.desat);
      g = lerp(g, y, p.desat);
      b = lerp(b, y, p.desat);
    }
    if (this._light && !this._onHead) {
      /* on paper, dimming fades towards the page instead of towards black */
      const c = inkOf([r, g, b]);
      return p.dim > 0.001 ? mix(c, PAPER, p.dim * 0.35) : c;
    }
    if (p.dim > 0.001) {
      const k = 1 - p.dim * 0.3;
      r *= k;
      g *= k;
      b *= k;
    }
    return [r, g, b];
  }

  /* Layered strokes approximate a neon tube without the cost of shadowBlur. */
  _neon(path, colour, w, a = 1, core = true) {
    if (this._light) {
      this._ink(path, colour, w, a, core);
      return;
    }
    const ctx = this._ctx;
    const A = a * this._alphaK;
    if (A <= 0.003) return;
    const glow = w;
    w = Math.max(w, this._minW);
    const style = Array.isArray(colour) ? rgba(colour, 1) : colour;
    ctx.strokeStyle = style;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    if (!this._icon) {
      ctx.globalAlpha = clamp(0.07 * A, 0, 1);
      ctx.lineWidth = glow * 5.5;
      ctx.stroke(path);
    }
    ctx.globalAlpha = clamp((this._icon ? 0.14 : 0.2) * A, 0, 1);
    ctx.lineWidth = Math.max(glow * 2.6, w * 1.7);
    ctx.stroke(path);
    ctx.globalAlpha = clamp(0.85 * A, 0, 1);
    ctx.lineWidth = w;
    ctx.stroke(path);
    if (core) {
      ctx.strokeStyle = Array.isArray(colour) ? rgba(mix(colour, C.white, 0.65), 1) : 'rgb(255,246,255)';
      ctx.globalAlpha = clamp(0.8 * A, 0, 1);
      ctx.lineWidth = Math.max(w * 0.36, this._minW * 0.6);
      ctx.stroke(path);
    }
    ctx.globalAlpha = 1;
  }

  /* Light theme stroke: a soft coloured drop-shadow below, a solid saturated ink line, and a satin highlight. */
  _ink(path, colour, w, a, core) {
    const ctx = this._ctx;
    const A = a * this._alphaK;
    if (A <= 0.003) return;
    const glow = w;
    w = Math.max(w * 1.5, this._minW * 1.2);
    ctx.strokeStyle = Array.isArray(colour) ? rgba(colour, 1) : colour;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    if (!this._icon) {
      ctx.save();
      ctx.translate(0, 5 + glow * 0.8);
      ctx.globalAlpha = clamp(0.1 * A, 0, 1);
      ctx.lineWidth = glow * 3 + 4;
      ctx.stroke(path);
      ctx.restore();
    }
    ctx.globalAlpha = clamp(0.95 * A, 0, 1);
    ctx.lineWidth = w;
    ctx.stroke(path);
    if (core) {
      ctx.strokeStyle = Array.isArray(colour) ? rgba(mix(colour, [255, 255, 255], 0.5), 1) : 'rgb(255,250,245)';
      ctx.globalAlpha = clamp(0.5 * A, 0, 1);
      ctx.lineWidth = Math.max(w * 0.3, this._minW * 0.5);
      ctx.stroke(path);
    }
    ctx.globalAlpha = 1;
  }

  _glow(x, y, r, colour, a) {
    const ctx = this._ctx;
    const A = a * this._alphaK * (this._light && !this._onHead ? 0.45 : 1);
    if (A <= 0.003) return;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, rgba(colour, A));
    g.addColorStop(1, rgba(colour, 0));
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }

  /* The dance: one move per routine section, scaled by weight w. Returns offsets for the pose. */
  _danceMoves(dt) {
    const cur = this._cur;
    const out = {
      w: 0, hop: 0, sway: 0, foot: 0, tilt: 0, aL: 0, aR: 0, spin: 1, happy: 0, smile: 0, mouth: 0,
      swirl: 0, wave: 0, wink: 0, spread: 0,
    };
    if (this._reduced) return out;
    let routine, b, w;
    if (this._fl) {
      const fl = this._fl;
      routine = fl.routine;
      b = fl.beat;
      w = clamp(fl.beat / 0.5, 0, 1) * clamp((fl.len - fl.beat) / 0.75, 0, 1);
      w = Math.max(w * (1 - cur.wDance), 0);
      if (fl.lastBeat !== Math.floor(b)) {
        fl.lastBeat = Math.floor(b);
        this._onBeat(w);
      }
    }
    if (!routine || cur.wDance > 0.5) {
      if (cur.wDance < 0.01) return out;
      routine = ROUTINES.dance;
      b = this._danceBeat % routineLength(routine);
      w = cur.wDance;
      const bi = Math.floor(this._danceBeat);
      if (bi !== this._lastBeat) {
        this._lastBeat = bi;
        this._onBeat(w);
      }
    }
    /* dancing to music: louder audio means livelier steps */
    const amp = 0.85 + clamp(this._audioS, 0, 1) * 0.55;
    let s0 = 0;
    let sec = routine[routine.length - 1];
    for (const s of routine) {
      if (b < s.until) {
        sec = s;
        break;
      }
      s0 = s.until;
    }
    const p = clamp((b - s0) / (sec.until - s0), 0, 1);
    const f = frac(b);
    const bounce = Math.sin(Math.PI * f);
    const swing = Math.cos(Math.PI * b);
    const side = Math.cos(Math.PI * Math.floor(b));
    switch (sec.kind) {
      case 'step': /* side-steps on the ribbon foot, body leaning against it, arms swinging in time */
        out.hop = 24 * bounce * amp;
        out.foot = 66 * side;
        out.sway = -20 * swing;
        out.tilt = 0.12 * swing;
        out.aL = 30 * swing;
        out.aR = 30 * swing;
        out.smile = 0.35;
        out.mouth = 0.2;
        out.wave = 12 * bounce;
        break;
      case 'wave': /* both hands up, waving, quick double taps of the foot */
        out.hop = 30 * bounce * amp;
        out.foot = 30 * Math.cos(Math.PI * Math.floor(b * 2));
        out.sway = 12 * Math.sin(Math.PI * b);
        out.tilt = 0.1 * Math.sin(Math.PI * b);
        out.aL = 50 + 18 * Math.sin(TAU * b);
        out.aR = -54 + 18 * Math.sin(TAU * b + Math.PI);
        out.smile = 0.6;
        out.mouth = 0.45;
        out.wave = 18 * bounce;
        out.spread = 0.25;
        break;
      case 'spin': /* a pirouette with a happy face and the ribbons swirling round */
        out.spin = Math.cos(TAU * ease(p));
        out.hop = 34 * Math.sin(Math.PI * p) * amp;
        out.aL = 18;
        out.aR = -22;
        out.happy = 1;
        out.smile = 0.6;
        out.mouth = 0.3;
        out.swirl = Math.sin(Math.PI * p);
        out.wave = 22 * Math.sin(Math.PI * p);
        out.spread = 0.35;
        break;
      case 'tada': /* land, arms up in a V, a wink */
        out.hop = 12 * bounce * (1 - p);
        out.aL = 52;
        out.aR = -58;
        out.tilt = 0.14 * ease(clamp(p * 2, 0, 1));
        out.smile = 0.7;
        out.mouth = 0.5;
        out.wink = Math.sin(Math.PI * clamp((p - 0.15) * 1.6, 0, 1));
        out.spread = 0.4;
        break;
    }
    out.w = w;
    for (const key in out) if (key !== 'w' && key !== 'spin') out[key] *= w;
    out.spin = lerp(1, out.spin, w);
    return out;
  }

  /* every landing squashes, splashes the pool and flops the ears */
  _onBeat(w) {
    const k = clamp(w, 0, 1);
    this._sp.sq.v += 3.2 * k;
    this._splash = Math.max(this._splash, k);
    this._sp.earL.v += 1.5 * k;
    this._sp.earR.v += 1.5 * k;
  }

  _computePose(m, dt) {
    const cur = this._cur;
    const ph = this._ph;
    const sp = this._sp;
    const reduced = this._reduced;
    const audio = this._audioS * cur.wTalk;
    const M = reduced ? 0 : 1;

    /* breathing: depth wanders as well as rate */
    const breath = (Math.sin(ph.breath) + 0.25 * Math.sin(ph.breath * 2 + 0.6)) * (0.8 + 0.35 * fbm(this._rt * 0.23 + 9)) * cur.breath;
    const D = this._danceMoves(dt);
    ph.swirl += dt * TAU * (D.swirl * 1.1 + D.w * 0.2);
    ph.sway += dt * TAU * D.w * 0.5;

    /* onsets in the music add an extra little hop */
    this._onsetCool -= dt;
    if (D.w > 0.3 && this._audioS - this._audioSlow > 0.14 && this._onsetCool <= 0) {
      this._onsetCool = 0.22;
      sp.hop.v += 140 * D.w;
    }

    const grooveK = cur.groove * M * (1 - D.w);
    const groove = Math.pow(Math.abs(Math.sin(ph.groove)), 1.5) * grooveK;
    const bob = Math.sin(ph.bob) * cur.bobAmp * m * (1 - D.w * 0.6) + cur.drop - groove * 7 + breath * 2.5;

    const hopS = Math.max(-6, sp.hop.step(0, dt));
    const hop = D.hop + hopS * M;
    const sq = sp.sq.step(0, dt) * M;
    const sqK = clamp(sq * 0.06, -0.14, 0.14);
    this._splash = Math.max(0, this._splash - dt * 2.2);

    const gazeX = sp.gazeX.x;
    const gazeY = sp.gazeY.x;
    /* the head turns a little towards where the eyes look; the ribbons follow behind it */
    const headDX = sp.headX.step((gazeX * 14 + D.sway * 0.6) * (reduced ? 0.3 : 1), dt);
    const lag = sp.lag.step(headDX, dt);
    const footX = sp.foot.step(D.foot, dt);
    const groupX = D.sway * 0.5;

    /* ears trail the head's vertical motion, so hops and bobs make them flop and settle */
    const headY = bob - hop;
    const vy = dt > 0 ? (headY - this._prevHeadY) / dt : 0;
    this._prevHeadY = headY;
    const earDrive = clamp(-vy * 0.0018, -0.5, 0.5) * M;
    const earL = sp.earL.step(earDrive, dt);
    const earR = sp.earR.step(earDrive, dt);
    const tiltS = sp.tilt.step(0, dt);

    let eyeOpen = cur.eyeOpen * (1 - this._blink);
    let lookX = gazeX;
    let lookY = gazeY;

    if (cur.wWatch > 0.01) {
      const cyc = frac(this._t / 7.5);
      const peek = cyc > 0.55 && cyc < 0.9 ? Math.sin(((cyc - 0.55) / 0.35) * Math.PI) : 0;
      eyeOpen += cur.wWatch * peek * 0.42;
      lookX += cur.wWatch * peek * (Math.sin(this._t * 0.9) * 0.9);
    }
    if (cur.wThink > 0.01) lookX += cur.wThink * Math.sin(this._t * 0.8) * 0.45;
    if (cur.wBrowse > 0.01) {
      const i = Math.floor(this._t * 0.7) % 6;
      const tx = i % 2 === 0 ? -0.85 : 0.85;
      this._browseLook += (tx - this._browseLook) * 0.12 * (1 - cur.dim * 0.6);
      lookX = lerp(lookX, this._browseLook, cur.wBrowse);
      lookY = lerp(lookY, (Math.floor(i / 2) - 1) * 0.55, cur.wBrowse);
    }
    if (cur.wCode > 0.01) lookX += cur.wCode * Math.sin(this._t * 3) * 0.08;

    /* a gentle head wander plus a curious tilt towards where it looks */
    const tilt = cur.tilt + Math.sin(ph.bob * 0.5) * 0.03 * m + audio * 0.04 + fbm(this._rt * 0.21 + 5) * 0.05 * m
      + gazeX * 0.05 + tiltS * M + D.tilt + groove * 0.03 * Math.sign(Math.sin(ph.groove * 0.5));
    const head = { x: HEAD.x + headDX, y: HEAD.y + bob, s: 1 + breath * 0.014, tilt };

    let aR = cur.aR + cur.waveR * Math.sin(ph.wave) * 22 * m;
    let aL = cur.aL + Math.sin(ph.bob + 1) * 3 * m + cur.wTalk * (Math.sin(this._t * 1.4) * 8 * m + audio * 14);
    aL += cur.typing * Math.sin(ph.type) * 5 * m;
    aR += cur.typing * Math.sin(ph.type + Math.PI) * 5 * m;
    aR += cur.wSuccess * Math.sin(ph.wave * 0.8) * 6 * m;
    aL -= cur.wSuccess * Math.sin(ph.wave * 0.8) * 6 * m;
    aL += breath * 2.5 + groove * 5;
    aR -= breath * 2.5 + groove * 5;
    aL = sp.aL.step(aL + D.aL, dt);
    aR = sp.aR.step(aR + D.aR, dt);
    /* fingers drag behind the arm's swing, and now and then wiggle */
    this._wiggle = Math.max(0, this._wiggle - dt * 0.9);
    const wig = this._wiggle > 0 ? Math.sin(this._rt * 22) * 0.3 * Math.sin(Math.PI * this._wiggle) : 0;
    const curlL = sp.fingerL.step(clamp(sp.aL.v * 0.004, -0.6, 0.6), dt) + wig;
    const curlR = sp.fingerR.step(clamp(-sp.aR.v * 0.004, -0.6, 0.6), dt) - wig;

    const happy = clamp(cur.happy + D.happy, 0, 1);
    let winkR = D.wink;
    if (D.spin < 0) winkR = 0;
    return {
      bob, breath, spinX: D.spin, hop, groupX, headDX, lag, footX,
      sqX: 1 + sqK * 0.7, sqY: 1 - sqK,
      eyeOpen: clamp(eyeOpen, 0, 1.15), lookX: clamp(lookX, -1, 1), lookY: clamp(lookY, -1, 1),
      head, aL, aR, curlL, curlR, audio, faceA: Math.max(0, D.spin),
      earL: earL + cur.earDroop, earR: earR + cur.earDroop,
      happy, winkR, smile: cur.smile + D.smile,
      mouthOpen: cur.mouthOpen + D.mouth, swirl: D.swirl, waveBoost: D.wave, dance: D.w,
      amberPulse: cur.amber * (0.55 + 0.45 * Math.sin(ph.pulse * 1.6)),
    };
  }

  _drawBackground(view) {
    const ctx = this._ctx;
    const half = view.s / 2 + 4;
    const g = ctx.createRadialGradient(500, 470, 20, 500, 500, 640);
    const t = this._tone([22, 14, 56]);
    g.addColorStop(0, rgba(t, 1));
    g.addColorStop(0.45, rgba(this._tone([10, 7, 30]), 1));
    g.addColorStop(1, 'rgb(3,2,10)');
    ctx.fillStyle = g;
    ctx.fillRect(view.x - half, view.y - half, half * 2, half * 2);
  }

  _drawVortex() {
    const cur = this._cur;
    const rings = this._icon ? 4 : 12;
    const cols = [C.violet, C.blue, C.magenta, C.violet, C.cyan];
    for (let i = 0; i < rings; i++) {
      const r = 150 + i * (this._icon ? 50 : 28);
      const dir = i % 2 ? 1 : -0.7;
      const rot = this._ph.swirl * (1 + i * 0.07) * dir + i * 1.3;
      const col = this._tone(cols[i % cols.length]);
      const segs = 3;
      for (let s = 0; s < segs; s++) {
        const a0 = rot + (s / segs) * TAU;
        const len = TAU / segs * (0.45 + 0.35 * Math.sin(i * 2.1 + s));
        const p = new Path2D();
        p.ellipse(CENTRE.x + 6, CENTRE.y + 10, r, r * 0.94, 0, a0, a0 + len);
        this._neon(p, col, 1.6, 0.16 + cur.wThink * 0.12 + this._pose.audio * 0.15, false);
      }
    }
  }

  _drawAura() {
    const cur = this._cur;
    const P = this._pose;
    const base = mix(this._tone(C.violet), this._tone(C.amber), P.amberPulse * 0.8);
    this._glow(HEAD.x, HEAD.y + P.bob, 300, base, 0.22);
    this._glow(TAIL_TIP.x, 680, 300, this._tone(C.magenta), 0.1 + P.audio * 0.2);
    if (cur.wApprove > 0.01) {
      this._glow(HEAD.x, HEAD.y + 80 + P.bob, 420, this._tone(C.amber), cur.wApprove * (0.12 + 0.18 * P.amberPulse));
    }
    if (cur.wWatch > 0.01) {
      const pulse = 0.5 + 0.5 * Math.sin(this._t * 1.4);
      this._glow(HEAD.x, HEAD.y + 40, 280, this._tone(C.cyan), cur.wWatch * 0.12 * pulse);
    }
  }

  _drawSonar() {
    const cur = this._cur;
    const ctx = this._ctx;
    if (cur.wWatch > 0.01) {
      for (let i = 0; i < 2; i++) {
        const p = frac(this._t / 4.2 + i * 0.5);
        const path = new Path2D();
        path.ellipse(CENTRE.x, CENTRE.y + 20, 160 + p * 360, (160 + p * 360) * 0.94, 0, 0, TAU);
        this._neon(path, this._tone(C.cyan), 2.2, cur.wWatch * 0.5 * (1 - p) * (p < 0.08 ? p / 0.08 : 1), false);
      }
      const sweep = this._t * 0.55;
      for (let j = 0; j < 10; j++) {
        const p = new Path2D();
        const a = sweep - j * 0.06;
        p.moveTo(CENTRE.x, CENTRE.y + 20);
        p.lineTo(CENTRE.x + Math.cos(a) * 470, CENTRE.y + 20 + Math.sin(a) * 440);
        ctx.strokeStyle = rgba(this._tone(C.cyan), 1);
        ctx.globalAlpha = cur.wWatch * 0.05 * (1 - j / 10) * this._alphaK * 2;
        ctx.lineWidth = Math.max(10, this._minW);
        ctx.stroke(p);
      }
      ctx.globalAlpha = 1;
    }
    if (cur.wApprove > 0.01) {
      for (let i = 0; i < 3; i++) {
        const p = frac(this._t / 1.8 + i / 3);
        const path = new Path2D();
        const r = 190 + p * 300;
        path.ellipse(HEAD.x, HEAD.y + 90 + this._pose.bob, r, r * 0.95, 0, 0, TAU);
        this._neon(path, this._tone(C.amber), 3, cur.wApprove * 0.7 * (1 - p), false);
      }
    }
  }

  /* ---------- shards and task layouts ---------- */

  _computeShards(m) {
    const cur = this._cur;
    const t = this._t;
    const sp = this._spread;
    const weights = [0, cur.wBrowse, cur.wWrite, cur.wCode, cur.wShop, cur.wCal];
    const taskSum = weights.reduce((a, b) => a + b, 0);
    weights[0] = Math.max(0, 1 - taskSum);
    const total = weights.reduce((a, b) => a + b, 0) || 1;
    const sx = (x) => CENTRE.x + (x - CENTRE.x) * sp;
    const sy = (y) => CENTRE.y + (y - CENTRE.y) * sp;
    const frames = [];

    for (let i = 0; i < this._shards.length; i++) {
      const sh = this._shards[i];
      const layouts = [];

      /* 0 â€” idle orbit: tumbling glass prisms */
      {
        const ang = sh.ang + this._ph.orbit * (i % 2 ? 1 : 0.85);
        const think = cur.wThink;
        const rx = sh.rx * (1 - think * 0.12);
        const ry = sh.ry * (1 - think * 0.12);
        let x = CENTRE.x + Math.cos(ang) * rx;
        let y = CENTRE.y + Math.sin(ang) * ry + Math.sin(t * 0.6 + sh.bob) * 10 * m;
        y += cur.wError * (70 + i * 6);
        const tumble = Math.cos(sh.tumble + t * sh.tumbleSpeed);
        const rot = sh.rot + t * sh.rotSpeed;
        const s = sh.size * (this._icon ? 0.85 : 1);
        const q = sh.kite.map(([kx, ky]) => {
          const px = kx * s * (0.35 + 0.65 * Math.abs(tumble));
          const py = ky * s;
          return [sx(x) + px * Math.cos(rot) - py * Math.sin(rot), sy(y) + px * Math.sin(rot) + py * Math.cos(rot)];
        });
        layouts.push({ q, z: -1, a: 1 - cur.wError * 0.4 });
      }

      /* 1 â€” browsing: scanning panes facing Jig */
      {
        const col = i % 2;
        const row = Math.floor(i / 2);
        let x, y, w, h, a = 1;
        if (row < 3) {
          x = col ? 838 : 172;
          y = 245 + row * 190 + Math.sin(t * 0.9 + i) * 8 * m;
          w = 88; h = 62;
        } else {
          x = col ? 760 : 250;
          y = 120 + (row - 3) * 690;
          w = 52; h = 36; a = 0.55;
        }
        const inner = 0.84;
        const corners = col
          ? [[-w, -h * inner], [w, -h], [w, h], [-w, h * inner]]
          : [[-w, -h], [w, -h * inner], [w, h * inner], [-w, h]];
        layouts.push({ q: corners.map(([px, py]) => [sx(x) + px * sp, sy(y) + py * sp]), z: -1, a });
      }

      /* 2 â€” writing: one page, one envelope, the rest drift as faint glints */
      {
        let q, a = 1;
        if (i === 0) {
          const x = 842, y = 470, w = 96, h = 124;
          q = [[-w, -h], [w, -h + 6], [w, h], [-w, h - 6]].map(([px, py]) => [sx(x) + px * sp, sy(y) + py * sp]);
        } else if (i === 1) {
          const x = 190, y = 300 + Math.sin(t * 1.3) * 10 * m, w = 66, h = 44;
          q = [[-w, -h], [w, -h], [w, h], [-w, h]].map(([px, py]) => [sx(x) + px * sp, sy(y) + py * sp]);
        } else {
          const ang = sh.ang + t * 0.15;
          const x = CENTRE.x + Math.cos(ang) * 440, y = CENTRE.y + Math.sin(ang) * 420;
          const s = 10;
          q = [[0, -s], [s * 0.6, 0], [0, s], [-s * 0.6, 0]].map(([px, py]) => [sx(x) + px, sy(y) + py]);
          a = 0.45;
        }
        layouts.push({ q, z: -1, a });
      }

      /* 3 â€” coding: a terminal pane and small orbiting pixels */
      {
        let q, a = 1;
        if (i === 0) {
          const x = 830, y = 560, w = 112, h = 92;
          q = [[-w, -h], [w, -h], [w, h], [-w, h]].map(([px, py]) => [sx(x) + px * sp, sy(y) + py * sp]);
        } else {
          const ang = sh.ang * 1.0 + t * 0.6;
          const x = CENTRE.x + Math.cos(ang) * 400, y = CENTRE.y - 40 + Math.sin(ang) * 360;
          const s = 9;
          q = [[-s, -s], [s, -s], [s, s], [-s, s]].map(([px, py]) => [sx(x) + px, sy(y) + py]);
          a = 0.7;
        }
        layouts.push({ q, z: -1, a });
      }

      /* 4 â€” shopping: a payment card and a ring of spinning coins */
      {
        let q, z = -1, a = 1;
        if (i === 0) {
          const x = 852, y = 512 + Math.sin(t * 1.6) * 6 * m, w = 96, h = 60, r = -0.16;
          q = [[-w, -h], [w, -h], [w, h], [-w, h]].map(([px, py]) => [
            sx(x) + (px * Math.cos(r) - py * Math.sin(r)) * sp,
            sy(y) + (px * Math.sin(r) + py * Math.cos(r)) * sp,
          ]);
          z = 1;
        } else {
          const ang = (i / 9) * TAU + t * 0.7;
          const x = CENTRE.x + Math.cos(ang) * 340, y = 745 + Math.sin(ang) * 90;
          const depth = Math.sin(ang);
          const s = 34 * (0.8 + depth * 0.2);
          const spin = Math.abs(Math.cos(t * 3 + i));
          q = [[-s * spin, -s], [s * spin, -s], [s * spin, s], [-s * spin, s]].map(([px, py]) => [sx(x) + px * sp, sy(y) + py * sp]);
          z = depth;
        }
        layouts.push({ q, z, a, coin: i > 0 });
      }

      /* 5 â€” scheduling: calendar page with day tiles */
      {
        let q;
        const cx = 828, cy = 480;
        if (i === 0) {
          const w = 108, h = 124;
          q = [[-w, -h], [w, -h], [w, h], [-w, h]].map(([px, py]) => [sx(cx) + px * sp, sy(cy) + py * sp]);
        } else {
          const k = i - 1;
          const gx = (k % 3) - 1;
          const gy = Math.floor(k / 3);
          const x = cx + gx * 64, y = cy - 26 + gy * 58, s = 24;
          q = [[-s, -s * 0.85], [s, -s * 0.85], [s, s * 0.85], [-s, s * 0.85]].map(([px, py]) => [sx(x) + px * sp, sy(y) + py * sp]);
        }
        layouts.push({ q, z: -1, a: 1 });
      }

      const q = [[0, 0], [0, 0], [0, 0], [0, 0]];
      let z = 0, a = 0;
      for (let L = 0; L < layouts.length; L++) {
        const w = weights[L] / total;
        if (w <= 0) continue;
        const lq = layouts[L].q;
        for (let v = 0; v < 4; v++) {
          q[v][0] += lq[v][0] * w;
          q[v][1] += lq[v][1] * w;
        }
        z += layouts[L].z * w;
        a += layouts[L].a * w;
      }
      frames.push({ q, z, a, wOrbit: weights[0] / total });
    }
    return frames;
  }

  _drawShards(frames, front) {
    const ctx = this._ctx;
    const cur = this._cur;
    for (let i = 0; i < frames.length; i++) {
      const f = frames[i];
      if ((f.z > 0) !== front) continue;
      const path = quadPath(f.q);
      const a = f.a * (this._icon ? 0.8 : 1);
      const coin = i > 0 ? cur.wShop : 0;
      const shardA = a * (1 - coin * 0.85);
      const g = ctx.createLinearGradient(f.q[0][0], f.q[0][1], f.q[2][0], f.q[2][1]);
      g.addColorStop(0, rgba(this._tone(C.cyan), 0.26 * shardA * this._alphaK));
      g.addColorStop(0.45, rgba(this._tone(C.violet), 0.12 * shardA * this._alphaK));
      g.addColorStop(0.7, rgba(this._tone(C.blue), 0.2 * shardA * this._alphaK));
      g.addColorStop(1, rgba(this._tone(C.magenta), 0.3 * shardA * this._alphaK));
      ctx.fillStyle = g;
      ctx.fill(path);
      this._neon(path, this._tone(mix(C.violet, C.white, 0.45)), 2, 0.85 * shardA);
      if (f.wOrbit > 0.01) {
        const facet = new Path2D();
        const c = quadPoint(f.q, 0.05, 0.05);
        facet.moveTo(f.q[0][0], f.q[0][1]);
        facet.lineTo(c[0], c[1]);
        facet.lineTo(f.q[2][0], f.q[2][1]);
        facet.moveTo(c[0], c[1]);
        facet.lineTo(f.q[3][0], f.q[3][1]);
        this._neon(facet, this._tone(C.cyan), 1, 0.45 * shardA * f.wOrbit, false);
        const edge = new Path2D();
        edge.moveTo(f.q[1][0], f.q[1][1]);
        edge.lineTo(f.q[2][0], f.q[2][1]);
        this._neon(edge, this._tone(C.amber), 2, 0.7 * shardA * f.wOrbit);
      }
    }
  }

  _drawTaskBack(frames) {
    const cur = this._cur;
    if (cur.wBrowse > 0.01) this._drawBrowse(frames, cur.wBrowse);
    if (cur.wWrite > 0.01) this._drawWrite(frames, cur.wWrite);
    if (cur.wCode > 0.01) this._drawCode(frames, cur.wCode);
    if (cur.wCal > 0.01) this._drawCalendar(frames, cur.wCal);
    if (cur.wShop > 0.01) this._drawCoins(frames, cur.wShop, false);
  }

  _drawTaskFront(frames) {
    const cur = this._cur;
    if (cur.wShop > 0.01) {
      this._drawCoins(frames, cur.wShop, true);
      this._drawCard(frames[0], cur.wShop);
    }
    if (cur.wWrite > 0.01) this._drawPen(frames, cur.wWrite);
  }

  _line(q, u0, v0, u1, v1) {
    const p = new Path2D();
    const a = quadPoint(q, u0, v0);
    const b = quadPoint(q, u1, v1);
    p.moveTo(a[0], a[1]);
    p.lineTo(b[0], b[1]);
    return p;
  }

  _drawBrowse(frames, w) {
    const ctx = this._ctx;
    const t = this._t;
    const active = Math.floor(t * 0.7) % 6;
    for (let i = 0; i < frames.length; i++) {
      const q = frames[i].q;
      const main = i < 6;
      const on = main && i === active;
      const A = w * (main ? 1 : 0.5) * (on ? 1.3 : 0.8);
      this._neon(this._line(q, -1, -0.62, 1, -0.62), this._tone(C.violet), 1.4, A * 0.8, false);
      for (let d = 0; d < 3; d++) {
        const p = new Path2D();
        const c = quadPoint(q, -0.86 + d * 0.11, -0.81);
        p.arc(c[0], c[1], 3.2, 0, TAU);
        ctx.fillStyle = rgba(this._tone([C.magenta, C.amber, C.cyan][d]), A * this._alphaK);
        ctx.fill(p);
      }
      if (!this._icon || main) {
        const lines = main ? 4 : 2;
        for (let l = 0; l < lines; l++) {
          const v = -0.3 + l * 0.32;
          const len = 0.35 + 0.5 * frac(Math.sin(i * 12.9 + l * 7.3) * 43758.5);
          this._neon(this._line(q, -0.78, v, -0.78 + len * 1.5, v), this._tone(l === 0 ? C.amber : C.cyan), 2, A * 0.55, false);
        }
      }
      if (main) {
        const sv = -0.5 + 1.46 * frac(t * 0.55 + i * 0.17);
        const top = Math.max(-0.6, sv - 0.4);
        const p0 = quadPoint(q, -0.97, top), p1 = quadPoint(q, 0.97, top);
        const p2 = quadPoint(q, 0.97, sv), p3 = quadPoint(q, -0.97, sv);
        const sweep = ctx.createLinearGradient(0, p0[1], 0, p3[1]);
        sweep.addColorStop(0, rgba(this._tone(C.cyan), 0));
        sweep.addColorStop(1, rgba(this._tone(C.cyan), (on ? 0.32 : 0.14) * w * this._alphaK));
        const band = new Path2D();
        band.moveTo(p0[0], p0[1]);
        band.lineTo(p1[0], p1[1]);
        band.lineTo(p2[0], p2[1]);
        band.lineTo(p3[0], p3[1]);
        band.closePath();
        ctx.fillStyle = sweep;
        ctx.fill(band);
        this._neon(this._line(q, -0.97, sv, 0.97, sv), this._tone(C.cyan), on ? 3.2 : 2.2, A * (on ? 1.1 : 0.6));
      }
      if (on) {
        const P = this._pose;
        const c = quadPoint(q, 0, 0);
        const ex = HEAD.x + (c[0] < HEAD.x ? -70 : 80);
        const ey = HEAD.y + P.bob + 30;
        const g = ctx.createLinearGradient(ex, ey, c[0], c[1]);
        g.addColorStop(0, rgba(this._tone(C.amber), 0.18 * w * this._alphaK));
        g.addColorStop(1, rgba(this._tone(C.cyan), 0.07 * w * this._alphaK));
        const beam = new Path2D();
        const top = quadPoint(q, c[0] < HEAD.x ? 1 : -1, -1);
        const bot = quadPoint(q, c[0] < HEAD.x ? 1 : -1, 1);
        beam.moveTo(ex, ey);
        beam.lineTo(top[0], top[1]);
        beam.lineTo(bot[0], bot[1]);
        beam.closePath();
        ctx.fillStyle = g;
        ctx.fill(beam);
      }
    }
  }

  _cursive(q, v, u0, u1, progress) {
    const pts = [];
    const n = 60;
    const loops = 7;
    let end = null;
    for (let j = 0; j <= n * progress; j++) {
      const s = j / n;
      const ang = s * loops * TAU;
      const u = lerp(u0, u1, s) - Math.sin(ang) * 0.045;
      const vv = v - Math.cos(ang) * 0.06 + Math.sin(s * 9) * 0.02;
      const p = quadPoint(q, u, vv);
      pts.push(p[0], p[1]);
      end = p;
    }
    return { path: pts.length >= 4 ? smoothPath(pts) : null, end };
  }

  _writingCycle() {
    const cyc = frac(this._t / 7);
    return { cyc, lineCount: 5, prog: cyc * 1.2 * 5 };
  }

  _drawWrite(frames, w) {
    const ctx = this._ctx;
    const page = frames[0].q;
    const { cyc, lineCount, prog } = this._writingCycle();
    const fade = cyc > 0.86 ? 1 - (cyc - 0.86) / 0.14 : 1;
    this._neon(this._line(page, -0.7, -0.78, 0.2, -0.78), this._tone(C.amber), 2.4, w * 0.9, false);
    for (let l = 0; l < lineCount; l++) {
      const p = clamp(prog - l, 0, 1);
      if (p <= 0) continue;
      const v = -0.5 + l * 0.34;
      const u1 = l === lineCount - 1 ? 0.2 : 0.78;
      const { path } = this._cursive(page, v, -0.78, u1, p);
      if (path) this._neon(path, this._tone(l % 2 ? C.magenta : C.cyan), 1.8, w * fade);
    }
    const env = frames[1].q;
    const flap = new Path2D();
    const a = quadPoint(env, -1, -1), b = quadPoint(env, 0, 0.15), c = quadPoint(env, 1, -1);
    flap.moveTo(a[0], a[1]);
    flap.lineTo(b[0], b[1]);
    flap.lineTo(c[0], c[1]);
    const sent = cyc > 0.86 ? Math.sin(((cyc - 0.86) / 0.14) * Math.PI) : 0;
    this._neon(flap, this._tone(C.amber), 2.4, w * (0.8 + sent));
    if (sent > 0.01) {
      const cen = quadPoint(env, 0, 0);
      this._glow(cen[0], cen[1], 90, this._tone(C.amber), w * 0.5 * sent);
      for (let s = 0; s < 3; s++) {
        const p = new Path2D();
        p.moveTo(cen[0] + 80 + s * 10, cen[1] - 30 + s * 30);
        p.lineTo(cen[0] + 120 + s * 10 + sent * 40, cen[1] - 30 + s * 30);
        this._neon(p, this._tone(C.amber), 2, w * sent, false);
      }
    }
    ctx.globalAlpha = 1;
  }

  _drawPen(frames, w) {
    const page = frames[0].q;
    const { cyc, lineCount, prog } = this._writingCycle();
    if (cyc > 0.86) return;
    const l = Math.min(lineCount - 1, Math.floor(prog));
    const p = clamp(prog - l, 0, 1);
    const v = -0.5 + l * 0.34;
    const u1 = l === lineCount - 1 ? 0.2 : 0.78;
    const { end } = this._cursive(page, v, -0.78, u1, Math.max(p, 0.02));
    const hand = this._handR;
    if (!end || !hand) return;
    const path = new Path2D();
    path.moveTo(hand[0], hand[1]);
    const mx = (hand[0] + end[0]) / 2, my = (hand[1] + end[1]) / 2 - 40;
    path.quadraticCurveTo(mx + Math.sin(this._t * 4) * 20, my, end[0], end[1]);
    this._neon(path, this._tone(C.amber), 3, w);
    this._glow(end[0], end[1], 26, this._tone(C.white), w * 0.9);
  }

  _drawCode(frames, w) {
    const q = frames[0].q;
    const t = this._t;
    this._neon(this._line(q, -1, -0.68, 1, -0.68), this._tone(C.violet), 1.4, w * 0.8, false);
    const prompt = new Path2D();
    const a = quadPoint(q, -0.9, -0.88), b = quadPoint(q, -0.82, -0.8), c = quadPoint(q, -0.9, -0.72);
    prompt.moveTo(a[0], a[1]);
    prompt.lineTo(b[0], b[1]);
    prompt.lineTo(c[0], c[1]);
    this._neon(prompt, this._tone(C.mint), 1.8, w, false);
    const cyc = frac(t / 5.5);
    const typed = cyc * CODE_LINES.length * 1.25;
    let caret = null;
    for (let l = 0; l < CODE_LINES.length; l++) {
      const p = clamp(typed - l, 0, 1);
      if (p <= 0) break;
      const [indent, ...tokens] = CODE_LINES[l];
      const v = -0.48 + l * 0.27;
      let u = -0.82 + indent * 0.16;
      const totalLen = tokens.reduce((s, tk) => s + tk[0], 0);
      let remaining = totalLen * p;
      for (const [len, col] of tokens) {
        const draw = Math.min(len, remaining);
        if (draw <= 0) break;
        const du = (draw / 100) * 1.7;
        this._neon(this._line(q, u, v, u + du, v), this._tone(col), 3.2, w * 0.85, false);
        u += du + 0.06;
        remaining -= len;
      }
      caret = [u, v];
    }
    if (caret && Math.sin(t * 9) > 0) {
      this._neon(this._line(q, caret[0], caret[1] - 0.09, caret[0], caret[1] + 0.09), this._tone(C.white), 2, w);
    }
  }

  _drawCoins(frames, w, front) {
    const ctx = this._ctx;
    for (let i = 1; i < frames.length; i++) {
      const f = frames[i];
      if ((f.z > 0) !== front) continue;
      const q = f.q;
      const c = quadPoint(q, 0, 0);
      const rx = Math.abs(q[1][0] - q[0][0]) / 2;
      const ry = Math.abs(q[3][1] - q[0][1]) / 2;
      const p = new Path2D();
      p.ellipse(c[0], c[1], Math.max(rx, 1), Math.max(ry, 1), 0, 0, TAU);
      const depthA = 0.55 + 0.45 * clamp(f.z, -1, 1);
      ctx.fillStyle = rgba(this._tone(C.orange), 0.22 * w * depthA * this._alphaK);
      ctx.fill(p);
      this._neon(p, this._tone(C.amber), 2.4, w * depthA);
      if (rx > ry * 0.45 && !this._icon) {
        ctx.font = `700 ${Math.round(ry * 1.25)}px "Segoe UI", system-ui, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = rgba(this._tone([255, 236, 190]), w * depthA * this._alphaK);
        ctx.save();
        ctx.translate(c[0], c[1]);
        ctx.scale(rx / ry, 1);
        ctx.fillText('Â£', 0, 2);
        ctx.restore();
      }
    }
  }

  _drawCard(f, w) {
    const ctx = this._ctx;
    const q = f.q;
    const chip = new Path2D();
    const c0 = quadPoint(q, -0.72, -0.35), c1 = quadPoint(q, -0.38, -0.35), c2 = quadPoint(q, -0.38, 0.12), c3 = quadPoint(q, -0.72, 0.12);
    chip.moveTo(c0[0], c0[1]);
    chip.lineTo(c1[0], c1[1]);
    chip.lineTo(c2[0], c2[1]);
    chip.lineTo(c3[0], c3[1]);
    chip.closePath();
    ctx.fillStyle = rgba(this._tone(C.amber), 0.35 * w * this._alphaK);
    ctx.fill(chip);
    this._neon(chip, this._tone(C.amber), 1.6, w, false);
    this._neon(this._line(q, -0.72, 0.5, 0.2, 0.5), this._tone(C.cyan), 3, w * 0.8, false);
    this._neon(this._line(q, 0.35, 0.5, 0.72, 0.5), this._tone(C.magenta), 3, w * 0.8, false);
    const s = -1.6 + 3.2 * frac(this._t / 2.4);
    const sh = new Path2D();
    const a = quadPoint(q, clamp(s, -1, 1), -1), b = quadPoint(q, clamp(s - 0.35, -1, 1), 1);
    sh.moveTo(a[0], a[1]);
    sh.lineTo(b[0], b[1]);
    if (s > -1 && s < 1.35) this._neon(sh, this._tone(C.white), 5, w * 0.5, false);
    const tick = frac(this._t / 2.4);
    if (tick > 0.6) {
      const k = clamp((tick - 0.6) / 0.15, 0, 1);
      const p = new Path2D();
      const t0 = quadPoint(q, 0.38, -0.3), t1 = quadPoint(q, 0.52, -0.1), t2 = quadPoint(q, 0.8, -0.6);
      p.moveTo(t0[0], t0[1]);
      p.lineTo(t1[0], t1[1]);
      p.lineTo(lerp(t1[0], t2[0], k), lerp(t1[1], t2[1], k));
      this._neon(p, this._tone(C.mint), 3, w);
    }
  }

  _drawCalendar(frames, w) {
    const ctx = this._ctx;
    const page = frames[0].q;
    const band = new Path2D();
    const b0 = quadPoint(page, -1, -1), b1 = quadPoint(page, 1, -1), b2 = quadPoint(page, 1, -0.62), b3 = quadPoint(page, -1, -0.62);
    band.moveTo(b0[0], b0[1]);
    band.lineTo(b1[0], b1[1]);
    band.lineTo(b2[0], b2[1]);
    band.lineTo(b3[0], b3[1]);
    band.closePath();
    ctx.fillStyle = rgba(this._tone(C.magenta), 0.3 * w * this._alphaK);
    ctx.fill(band);
    for (const u of [-0.5, 0.5]) {
      const r = new Path2D();
      const c = quadPoint(page, u, -1);
      r.ellipse(c[0], c[1] - 4, 6, 12, 0, 0, TAU);
      this._neon(r, this._tone(C.cyan), 2, w);
    }
    const tick = Math.floor(this._t / 0.75) % 9;
    const booked = 5;
    for (let i = 1; i < frames.length; i++) {
      const k = i - 1;
      const q = frames[i].q;
      const c = quadPoint(q, 0, 0);
      if (k === booked) {
        this._glow(c[0], c[1], 40, this._tone(C.amber), w * 0.6);
        const p = new Path2D();
        const t0 = quadPoint(q, -0.5, 0), t1 = quadPoint(q, -0.1, 0.45), t2 = quadPoint(q, 0.55, -0.45);
        p.moveTo(t0[0], t0[1]);
        p.lineTo(t1[0], t1[1]);
        p.lineTo(t2[0], t2[1]);
        this._neon(p, this._tone(C.amber), 2.6, w);
      } else if (k === tick) {
        this._neon(quadPath(q), this._tone(C.cyan), 3, w);
      } else {
        const p = new Path2D();
        p.arc(c[0], c[1], 3, 0, TAU);
        ctx.fillStyle = rgba(this._tone(C.violet), 0.8 * w * this._alphaK);
        ctx.fill(p);
      }
    }
    const P = this._pose;
    const cx = HEAD.x, cy = HEAD.y + P.bob;
    const ring = new Path2D();
    ring.ellipse(cx, cy, 228, 218, 0, 0, TAU);
    this._neon(ring, this._tone(C.violet), 1.6, w * 0.55, false);
    for (let h = 0; h < 12; h++) {
      const a = (h / 12) * TAU;
      const p = new Path2D();
      p.moveTo(cx + Math.cos(a) * 214, cy + Math.sin(a) * 204);
      p.lineTo(cx + Math.cos(a) * 232, cy + Math.sin(a) * 222);
      this._neon(p, this._tone(h % 3 ? C.violet : C.amber), h % 3 ? 1.4 : 2.6, w * 0.8, false);
    }
    const ha = this._t * 0.9 - Math.PI / 2;
    const hand = new Path2D();
    hand.ellipse(cx, cy, 228, 218, 0, ha - 0.5, ha);
    this._neon(hand, this._tone(C.cyan), 3.4, w);
  }

  /* ---------- character ---------- */

  _drawBase() {
    const t = this._t;
    const cols = [C.violet, C.magenta, C.cyan, C.violet, C.blue];
    const ctx = this._ctx;
    const P = this._pose;
    const X = TAIL_TIP.x + P.footX + P.groupX;
    /* the shadow shrinks as Jig hops up */
    const lift = clamp(P.hop / 60, 0, 0.6);
    if (this._light) {
      /* a soft plum ground shadow anchors Jig on the page */
      const g = ctx.createRadialGradient(X, TAIL_TIP.y + 14, 4, X, TAIL_TIP.y + 14, 190);
      g.addColorStop(0, rgba(INK, 0.22 * this._alphaK * (1 - lift * 0.6)));
      g.addColorStop(1, rgba(INK, 0));
      ctx.save();
      ctx.translate(X, TAIL_TIP.y + 14);
      ctx.scale(1 - lift * 0.4, 0.24 * (1 - lift * 0.4));
      ctx.translate(-X, -(TAIL_TIP.y + 14));
      ctx.fillStyle = g;
      ctx.fillRect(X - 190, TAIL_TIP.y + 14 - 190, 380, 380);
      ctx.restore();
    }
    for (let i = 0; i < 5; i++) {
      const rx = 34 + i * 30;
      const p = new Path2D();
      p.ellipse(X, TAIL_TIP.y + 6, rx, rx * 0.26, 0, 0, TAU);
      ctx.setLineDash([rx * 0.9, rx * 0.5]);
      ctx.lineDashOffset = -this._ph.swirl * rx * (i % 2 ? 1.6 : -1.2) * 2 - t * 20;
      this._neon(p, this._tone(cols[i]), 2.2 - i * 0.25, 0.65 - i * 0.09, false);
    }
    ctx.setLineDash([]);
    if (this._splash > 0.01 && !this._icon) {
      /* each landing sends a ripple across the pool */
      const k = 1 - this._splash;
      const p = new Path2D();
      p.ellipse(X, TAIL_TIP.y + 6, 40 + k * 170, (40 + k * 170) * 0.26, 0, 0, TAU);
      this._neon(p, this._tone(C.cyan), 2.6, this._splash * 0.8, false);
    }
    this._glow(X, TAIL_TIP.y + 4, 60 + this._splash * 30, this._tone(C.magenta), 0.5 + this._splash * 0.3);
  }

  _drawTail() {
    const ctx = this._ctx;
    const cur = this._cur;
    const P = this._pose;
    const t = this._t;
    const ribbons = this._icon ? RIBBONS.filter((_, i) => i === 0 || i === 3 || i === 4 || i === 7) : RIBBONS;
    const iconK = this._icon ? 0.8 : 1;
    const n = this._icon ? 22 : 40;
    const flare = cur.flare * (1 + P.breath * 0.03);
    const waveAmp = cur.wave + P.audio * 46 + P.waveBoost;
    const swirlBoost = 1 + cur.wThink * 0.6 + P.swirl * 0.5;
    /* the top of the body moves with the head, the middle trails behind it and the tip follows the foot */
    const follow = (s) => lerp(P.headDX, P.lag, ease(clamp(s * 1.6, 0, 1))) * (1 - s * s) + P.footX * s * s;
    for (const r of ribbons) {
      const centre = [];
      const left = [];
      const right = [];
      for (let j = 0; j < n; j++) {
        const s = j / (n - 1);
        const env = Math.pow(Math.sin(Math.PI * Math.pow(s, 0.62)), 1.25);
        const y = TAIL_TOP + s * (TAIL_TIP.y - TAIL_TOP) + P.bob * (1 - s) * 0.95 + cur.drop * 0.15 * s;
        const sway = cur.sway * Math.sin(s * 2.6 - this._ph.sway + r.ph * 0.25) * (0.35 + s * 0.8) * (this._reduced ? 0.3 : 1);
        /* the body is an S-swirl: it leans left under the head, then sweeps right before the tip */
        const ess = 95 * flare * Math.sin(s * TAU * 0.92 + 0.35) * Math.sin(Math.PI * s);
        const curl = Math.sin(s * 4.2 * swirlBoost + r.ph + this._ph.swirl * 2.2);
        const side = r.o < 0 ? 1.08 + 0.12 * Math.sin(s * 5) : 0.95;
        const lateral = 285 * flare * env * (r.o * 0.74 * side + 0.3 * curl);
        const ripple = waveAmp * env * Math.sin(s * 9 - this._ph.sway * 1.6 + r.ph);
        const x = lerp(HEAD.x + r.o * 16, TAIL_TIP.x, s) - ess + sway + lateral + ripple + follow(s);
        centre.push(x, y);
      }
      const band = r.bw * (this._icon ? 1.4 : 1);
      for (let j = 0; j < n; j++) {
        const i0 = Math.max(0, j - 1) * 2;
        const i1 = Math.min(n - 1, j + 1) * 2;
        const dx = centre[i1] - centre[i0];
        const dy = centre[i1 + 1] - centre[i0 + 1];
        const len = Math.hypot(dx, dy) || 1;
        const s = j / (n - 1);
        const bw = band * Math.sin(Math.PI * Math.min(1, s * 1.15 + 0.04)) * (1 + P.audio * 0.6);
        const nx = -dy / len * bw;
        const ny = dx / len * bw;
        left.push(centre[j * 2] + nx, centre[j * 2 + 1] + ny);
        right.push(centre[j * 2] - nx, centre[j * 2 + 1] - ny);
      }
      const col = this._tone(r.c, P.amberPulse * 0.25);
      const bandPath = new Path2D();
      bandPath.moveTo(left[0], left[1]);
      for (let j = 2; j < left.length; j += 2) bandPath.lineTo(left[j], left[j + 1]);
      for (let j = right.length - 2; j >= 0; j -= 2) bandPath.lineTo(right[j], right[j + 1]);
      bandPath.closePath();
      ctx.fillStyle = rgba(col, (this._light ? 0.42 : 0.24) * iconK * this._alphaK * (1 + P.audio * 0.5));
      ctx.fill(bandPath);
      this._neon(smoothPath(centre), col, this._icon ? 5 : 3.8, (1 + P.audio * 0.3) * iconK);
      if (!this._icon) this._neon(smoothPath(left), col, 1.2, 0.55, false);
    }
    if (!this._icon) {
      /* fine filaments through the core of the body */
      for (let f = 0; f < 4; f++) {
        const pts = [];
        for (let j = 0; j < n; j++) {
          const s = j / (n - 1);
          const env = Math.pow(Math.sin(Math.PI * Math.pow(s, 0.62)), 1.25);
          const y = TAIL_TOP + s * (TAIL_TIP.y - TAIL_TOP) + P.bob * (1 - s) * 0.95;
          const ess = 95 * flare * Math.sin(s * TAU * 0.92 + 0.35) * Math.sin(Math.PI * s);
          const x = lerp(HEAD.x, TAIL_TIP.x, s) - ess + cur.sway * Math.sin(s * 2.6 - this._ph.sway) * (0.35 + s * 0.8)
            + 90 * flare * env * Math.sin(s * 6 + f * 1.7 + this._ph.swirl * 3 + t * 0.3) + follow(s);
          pts.push(x, y);
        }
        this._neon(smoothPath(pts), this._tone([C.white, C.cyan, C.pink, C.amber][f]), 0.9, 0.5, false);
      }
    }
  }

  /* Arm geometry: the arm curve, the hand, and each finger as [start, control, end]. */
  _armGeom(sx, sy, angDeg, len, bend, spread, side, drag) {
    const a = angDeg * DEG;
    const hx = sx + Math.cos(a) * len;
    const hy = sy + Math.sin(a) * len;
    const nx = -Math.sin(a), ny = Math.cos(a);
    const cx = (sx + hx) / 2 + nx * bend;
    const cy = (sy + hy) / 2 + ny * bend;
    const dir = Math.atan2(hy - cy, hx - cx);
    const curl = (side > 0 ? this._cur.waveR * Math.sin(this._ph.wave * 2) * 0.35 : 0) + drag;
    const fingers = [];
    const offs = [-1.5, -0.5, 0.5, 1.5];
    for (let k = 0; k < 4; k++) {
      const fa = dir + offs[k] * 0.3 * spread;
      const fl = [27, 34, 33, 26][k] * (this._icon ? 1.15 : 1);
      const ca = fa + side * (0.25 + curl);
      fingers.push([hx + Math.cos(ca) * fl * 0.6, hy + Math.sin(ca) * fl * 0.6, hx + Math.cos(fa) * fl, hy + Math.sin(fa) * fl]);
    }
    const ta = dir - side * 1.25 * Math.max(0.6, spread);
    const thumb = [hx + Math.cos(ta) * 20, hy + Math.sin(ta) * 20];
    return { sx, sy, cx, cy, hx, hy, dir, fingers, thumb };
  }

  /* How far the arm stays outside the head, as the smallest elliptical distance (1 = on the margin). The shoulder
     sits at the chin, so the margin ramps in along the upper arm. */
  _armClearance(g, head) {
    const c = Math.cos(-head.tilt), s = Math.sin(-head.tilt);
    /* at icon sizes the margin grows so it is still a couple of pixels wide */
    const M = Math.max(ARM_MARGIN, (this._minW || 0) * 2.2);
    const d = (x, y, m = M) => {
      const px = x - head.x, py = y - head.y;
      const lx = px * c - py * s, ly = px * s + py * c;
      return Math.hypot(lx / (HEAD.rx * head.s + m), ly / (HEAD.ry * head.s + m));
    };
    let min = Infinity;
    for (let i = 1; i <= 16; i++) {
      const t = i / 16;
      const u = 1 - t;
      const x = u * u * g.sx + 2 * u * t * g.cx + t * t * g.hx;
      const y = u * u * g.sy + 2 * u * t * g.cy + t * t * g.hy;
      min = Math.min(min, d(x, y, M * Math.min(1, t * 2.5)));
    }
    for (const f of g.fingers) {
      for (const t of [0.25, 0.5, 0.75, 1]) {
        const u = 1 - t;
        min = Math.min(min, d(u * u * g.hx + 2 * u * t * f[0] + t * t * f[2], u * u * g.hy + 2 * u * t * f[1] + t * t * f[3]));
      }
    }
    return Math.min(min, d(g.thumb[0], g.thumb[1]), d((g.hx + g.thumb[0]) / 2, (g.hy + g.thumb[1]) / 2));
  }

  /* Arms are drawn behind the head, so swing any arm that would pass behind it outwards until it is clear. */
  _clearArm(sx, sy, angDeg, len, bend, spread, side, drag) {
    const head = this._pose.head;
    let ang = angDeg;
    let g = this._armGeom(sx, sy, ang, len, bend, spread, side, drag);
    let clear = this._armClearance(g, head);
    for (let i = 0; i < 90 && clear < 1; i++) {
      ang += side < 0 ? -1.5 : 1.5;
      g = this._armGeom(sx, sy, ang, len, bend, spread, side, drag);
      clear = this._armClearance(g, head);
    }
    this._armClear = Math.min(this._armClear, clear);
    return g;
  }

  _drawArm(g) {
    const ctx = this._ctx;
    const arm = new Path2D();
    arm.moveTo(g.sx, g.sy);
    arm.quadraticCurveTo(g.cx, g.cy, g.hx, g.hy);
    const fingers = new Path2D();
    for (const f of g.fingers) {
      fingers.moveTo(g.hx, g.hy);
      fingers.quadraticCurveTo(f[0], f[1], f[2], f[3]);
    }
    fingers.moveTo(g.hx, g.hy);
    fingers.lineTo(g.thumb[0], g.thumb[1]);
    if (this._debugLayer === 'arms') {
      ctx.strokeStyle = 'rgb(0,255,0)';
      ctx.lineCap = 'round';
      ctx.lineWidth = Math.max(6.5, this._minW);
      ctx.stroke(arm);
      ctx.lineWidth = Math.max(3.6, this._minW);
      ctx.stroke(fingers);
      return [g.hx, g.hy];
    }
    const lg = ctx.createLinearGradient(g.sx, g.sy, g.hx, g.hy);
    lg.addColorStop(0, rgba(this._tone(C.violet), 1));
    lg.addColorStop(0.6, rgba(this._tone(C.blue), 1));
    lg.addColorStop(1, rgba(this._tone(mix(C.cyan, C.white, 0.3)), 1));
    this._neon(arm, lg, 6.5, 1);
    this._neon(fingers, this._tone(mix(C.cyan, C.violet, 0.4)), 3.6, 1);
    this._glow(g.hx, g.hy, 34, this._tone(C.cyan), 0.4);
    return [g.hx + Math.cos(g.dir) * 30, g.hy + Math.sin(g.dir) * 30];
  }

  _drawArms() {
    const P = this._pose;
    const cur = this._cur;
    const yL = SHOULDER_L.y + P.bob * 0.9 - P.breath * 2;
    const yR = SHOULDER_R.y + P.bob * 0.9 - P.breath * 2;
    const dx = P.headDX * 0.85;
    const spread = 1 + (P.dance || 0) * 0.15;
    this._armClear = Infinity;
    this._drawArm(this._clearArm(SHOULDER_L.x + dx, yL, P.aL, cur.lL, cur.bL, cur.spreadL * spread, -1, P.curlL));
    this._handR = this._drawArm(this._clearArm(SHOULDER_R.x + dx, yR, P.aR, cur.lR, cur.bR, cur.spreadR * spread, 1, P.curlR));
  }

  /* A soft petal: wide in the middle, rounded at the tip, its tip curling by `bend` (local axis is -y). */
  _leafPath(L, W, bend) {
    const p = new Path2D();
    const tx = bend * L * 0.32;
    const ty = -L;
    p.moveTo(-W * 0.34, 0);
    p.bezierCurveTo(-W * 1.12, -L * 0.3, tx - W * 0.46, ty + L * 0.14, tx, ty);
    p.bezierCurveTo(tx + W * 0.46, ty + L * 0.14, W * 1.12, -L * 0.3, W * 0.34, 0);
    p.quadraticCurveTo(0, L * 0.08, -W * 0.34, 0);
    return p;
  }

  _drawEar(ear, out, amber) {
    const ctx = this._ctx;
    const { L, W } = ear;
    ctx.save();
    ctx.translate(ear.x, ear.y);
    ctx.rotate(ear.dir * (ear.ang + out * ear.give));
    const bend = ear.dir * (0.55 + out * 0.5);
    const p = this._leafPath(L, W, bend);
    const g = ctx.createLinearGradient(0, 0, bend * L * 0.32, -L);
    g.addColorStop(0, rgba(this._tone([46, 18, 96]), 1));
    g.addColorStop(0.45, rgba(this._tone([150, 52, 190], amber * 0.5), 1));
    g.addColorStop(1, rgba(this._tone([200, 90, 220], amber * 0.4), 1));
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = clamp(0.6 + this._alphaK * 0.35, 0, 1);
    ctx.fillStyle = g;
    ctx.fill(p);
    /* the warm orange-gold glow inside the leaf */
    const inner = this._leafPath(L, W, bend);
    const ig = ctx.createLinearGradient(0, -L * 0.1, bend * L * 0.3, -L * 0.88);
    ig.addColorStop(0, rgba(this._tone(C.orange, amber * 0.3), 0));
    ig.addColorStop(0.35, rgba(this._tone(C.orange, amber * 0.3), 0.75));
    ig.addColorStop(1, rgba(this._tone([255, 214, 140]), 0.95));
    ctx.save();
    ctx.translate(bend * L * 0.03, -L * 0.14);
    ctx.scale(0.56, 0.74);
    ctx.fillStyle = ig;
    ctx.fill(inner);
    ctx.restore();
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = this._addOp;
    this._neon(p, this._tone(C.violet, amber * 0.4), ear.small ? 2.4 : 3, 1);
    if (!ear.small || !this._icon) {
      const rib = new Path2D();
      rib.moveTo(0, -L * 0.06);
      rib.quadraticCurveTo(bend * L * 0.04, -L * 0.5, bend * L * 0.26, -L * 0.86);
      this._neon(rib, this._tone(C.amber, amber * 0.4), ear.small ? 1.6 : 2.4, 0.9);
    }
    ctx.restore();
  }

  _drawHead() {
    const ctx = this._ctx;
    const cur = this._cur;
    const P = this._pose;
    const h = P.head;
    const amber = P.amberPulse;

    ctx.save();
    ctx.translate(h.x, h.y);
    ctx.rotate(h.tilt);
    ctx.scale(h.s, h.s);

    /* two soft leaf ears and two small leaflets, each on its own spring; dir -1 is Jig's right (screen left) */
    const perk = cur.wApprove * Math.sin(this._ph.wave) * 0.04;
    const ears = [
      { x: -84, y: -112, ang: 0.62, L: 138, W: 62, dir: -1, give: 0.9, spring: P.earL, small: false },
      { x: 86, y: -114, ang: 0.56, L: 142, W: 62, dir: 1, give: 0.9, spring: P.earR, small: false },
      { x: -146, y: -52, ang: 1.25, L: 66, W: 30, dir: -1, give: 1.3, spring: P.earL * 1.2 + 0.05, small: true },
      { x: 142, y: -64, ang: 1.15, L: 62, W: 28, dir: 1, give: 1.3, spring: P.earR * 1.2 + 0.05, small: true },
    ];
    for (const ear of ears) this._drawEar(ear, ear.spring - perk, amber);

    const headPath = new Path2D();
    headPath.ellipse(0, 0, HEAD.rx, HEAD.ry, 0, 0, TAU);
    /* the head stays a dark plum in both themes: it is the character's silhouette and frames the amber eyes */
    this._onHead = true;
    const fill = ctx.createRadialGradient(-30, -40, 10, 0, 0, HEAD.rx + 10);
    if (this._light) {
      ctx.save();
      ctx.globalAlpha = 0.16;
      ctx.fillStyle = rgba(this._tone([90, 40, 110]), 1);
      ctx.translate(0, 18);
      ctx.fill(headPath);
      ctx.restore();
      fill.addColorStop(0, rgba(this._tone([92, 52, 128]), 1));
      fill.addColorStop(0.55, rgba(this._tone([44, 20, 66]), 1));
      fill.addColorStop(1, rgba(this._tone([30, 12, 46]), 1));
    } else {
      fill.addColorStop(0, rgba(this._tone([24, 16, 58]), 1));
      fill.addColorStop(0.7, rgba(this._tone([10, 6, 28]), 1));
      fill.addColorStop(1, rgba(this._tone([18, 8, 46]), 1));
    }
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = fill;
    ctx.fill(headPath);
    this._headFill = fill;
    this._onHead = false;

    ctx.globalCompositeOperation = this._addOp;
    const rim = ctx.createConicGradient(-Math.PI / 2, 0, 0);
    const rc = (c) => rgba(this._tone(c, amber * 0.55), 1);
    rim.addColorStop(0, rc(C.cyan));
    rim.addColorStop(0.18, rc(C.blue));
    rim.addColorStop(0.35, rc(C.magenta));
    rim.addColorStop(0.55, rc(C.violet));
    rim.addColorStop(0.75, rc(C.violet));
    rim.addColorStop(0.9, rc(C.magenta));
    rim.addColorStop(1, rc(C.cyan));
    this._neon(headPath, rim, 4.2, 1);
    this._onHead = true;
    const sheen = new Path2D();
    sheen.ellipse(0, 0, HEAD.rx - 16, HEAD.ry - 16, 0, Math.PI * 1.08, Math.PI * 1.42);
    this._neon(sheen, this._tone(C.violet), 1.6, 0.5, false);

    this._drawFace();
    this._onHead = false;
    ctx.restore();
    ctx.globalCompositeOperation = this._addOp;
  }

  /* eyelids are filled with the head's own gradient, which lives in head space, not eye space */
  _fillLid(r, lid, ex, ey) {
    const ctx = this._ctx;
    const shifted = new Path2D();
    shifted.addPath(lid, new DOMMatrix([1, 0, 0, 1, ex, ey]));
    /* a touch wider than the eye so the iris's anti-aliased rim never peeks out round a closed lid */
    const bound = new Path2D();
    bound.arc(0, 0, r + 2.5, 0, TAU);
    ctx.save();
    ctx.clip(bound);
    ctx.translate(-ex, -ey);
    ctx.fillStyle = this._headFill;
    ctx.fill(shifted);
    ctx.restore();
  }

  _drawFace() {
    const ctx = this._ctx;
    const cur = this._cur;
    const P = this._pose;
    const faceA = P.faceA;
    if (faceA <= 0.01) return;
    const r = 50;
    const lx = P.lookX, ly = P.lookY;
    const happy = P.happy;
    const eyes = [[-80, 26], [80, 26]];
    /* the eyes stay warm amber even when the rest of Jig cools to blue */
    const cool = cur.cool;
    cur.cool = cool * 0.3;
    try {
      for (let e = 0; e < 2; e++) {
        /* a wink is the same cheerful upturned arc as a happy eye, on one side */
        const open = P.eyeOpen;
        const closedK = e === 1 ? Math.max(happy, P.winkR) : happy;
        const ex = eyes[e][0] + lx * 8;
        const ey = eyes[e][1] + ly * 7;
        ctx.save();
        ctx.translate(ex, ey);
        const normalA = faceA * (1 - closedK);
        if (normalA > 0.01 && open > 0.04) {
          ctx.globalCompositeOperation = this._addOp;
          this._glow(0, 0, r * 1.75, this._tone(C.orange, P.amberPulse * 0.3), 0.42 * normalA * clamp(open * 1.6 - 0.2, 0, 1));
          ctx.globalCompositeOperation = 'source-over';
          ctx.globalAlpha = normalA;
          const eye = new Path2D();
          eye.arc(0, 0, r, 0, TAU);
          const ig = ctx.createRadialGradient(lx * 11, ly * 10, 4, 0, 0, r);
          const amberT = this._tone(C.amber);
          ig.addColorStop(0, 'rgb(28,8,4)');
          ig.addColorStop(0.36, 'rgb(76,22,8)');
          ig.addColorStop(0.64, rgba(this._tone(C.orange), 1));
          ig.addColorStop(0.9, rgba(mix(amberT, C.white, 0.38), 1));
          ig.addColorStop(1, rgba(amberT, 1));
          ctx.fillStyle = ig;
          ctx.fill(eye);
          const pupil = new Path2D();
          pupil.arc(lx * r * 0.24, ly * r * 0.22, r * 0.45, 0, TAU);
          ctx.fillStyle = 'rgb(16,6,22)';
          ctx.fill(pupil);
          /* two catch-lights that stay put as the eye moves: they are reflections of the light */
          const hl = new Path2D();
          hl.arc(-r * 0.28 + lx * 4, -r * 0.3 + ly * 3, r * 0.25, 0, TAU);
          hl.moveTo(r * 0.38 + lx * 4, r * 0.26 + ly * 3);
          hl.arc(r * 0.28 + lx * 4, r * 0.26 + ly * 3, r * 0.1, 0, TAU);
          ctx.fillStyle = 'rgba(255,250,240,0.96)';
          ctx.fill(hl);
          if (!this._icon) {
            const glint = new Path2D();
            glint.arc(r * 0.02 + lx * 4, -r * 0.52 + ly * 3, r * 0.055, 0, TAU);
            ctx.fillStyle = 'rgba(255,250,240,0.7)';
            ctx.fill(glint);
          }

          /* eyelids: head-coloured fill so they read at any size */
          if (open < 0.999 || cur.sad > 0.01) {
            /* the lid's edge is a soft downward curve, so a half-closed eye looks content rather than sly */
            const lidY = -r - 2 + (2 * r + 10) * clamp(1 - open, 0, 1) + cur.sad * r * 0.18;
            const slant = cur.sad * 0.32 * (e === 0 ? -1 : 1);
            const lid = new Path2D();
            lid.moveTo(-r - 4, -2 * r);
            lid.lineTo(r + 4, -2 * r);
            lid.lineTo(r + 4, lidY - r * 0.45 + slant * r);
            lid.quadraticCurveTo(0, lidY + r * 0.3, -r - 4, lidY - r * 0.45 - slant * r);
            lid.closePath();
            this._fillLid(r, lid, ex, ey);
          }
          ctx.globalAlpha = 1;
          ctx.globalCompositeOperation = this._addOp;
          this._neon(eye, this._tone(C.amber, 0.2), 2.4, normalA * clamp(open * 3 - 0.6, 0, 1) * (0.6 + 0.4 * Math.min(1, open)), false);
        }
        if (normalA > 0.01 && open < 0.3) {
          /* closed eyes are soft, contented curves */
          const k = 1 - open / 0.3;
          const lash = new Path2D();
          lash.moveTo(-r * 0.78, 6);
          lash.quadraticCurveTo(0, r * 0.5, r * 0.78, 6);
          ctx.globalCompositeOperation = this._addOp;
          this._neon(lash, this._tone(C.amber), 4.4, normalA * k);
        }
        if (closedK > 0.01) {
          const arc = new Path2D();
          arc.moveTo(-r * 0.78, r * 0.25);
          arc.quadraticCurveTo(0, -r * 0.85, r * 0.78, r * 0.25);
          ctx.globalCompositeOperation = this._addOp;
          this._neon(arc, this._tone(C.amber), 6.5, closedK * faceA);
          this._glow(0, -6, r * 1.4, this._tone(C.orange), 0.35 * closedK * faceA);
        }
        if (cur.brow > 0.01) {
          /* worried brows lift in the middle: apologetic, never cross */
          const inner = e === 0 ? 1 : -1;
          const brow = new Path2D();
          const by = -r - 18 - ly * 3;
          brow.moveTo(-inner * r * 0.62, by + 6);
          brow.quadraticCurveTo(inner * r * 0.05, by - 4 - cur.brow * 6, inner * r * 0.62, by - cur.brow * 16);
          ctx.globalCompositeOperation = this._addOp;
          this._neon(brow, this._tone(C.amber), 4.2, cur.brow * faceA * 0.9);
        }
        ctx.restore();
      }
    } finally {
      cur.cool = cool;
    }

    /* mouth: a filled amber crescent that opens with the voice level */
    const smile = P.smile;
    const openM = clamp(P.mouthOpen + P.audio * 1.1, 0, 1.2);
    const mx = 10, my = 84;
    const mw = 20 + openM * 9 + Math.max(0, smile - 1) * 4;
    const mouth = new Path2D();
    mouth.moveTo(mx - mw, my - smile * 3);
    mouth.quadraticCurveTo(mx, my + smile * 6 - openM * 10, mx + mw, my - smile * 3);
    mouth.quadraticCurveTo(mx, my + smile * 16 + openM * 46, mx - mw, my - smile * 3);
    mouth.closePath();
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = faceA;
    const mg = ctx.createLinearGradient(0, my - 10, 0, my + 30);
    mg.addColorStop(0, rgba(this._tone(C.amber), 1));
    mg.addColorStop(1, rgba(this._tone(C.orange), 1));
    ctx.fillStyle = openM > 0.15 ? 'rgb(40,10,30)' : mg;
    ctx.fill(mouth);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = this._addOp;
    this._neon(mouth, this._tone(C.amber), 2.4, faceA * 0.9);
    if (openM > 0.15) {
      const tongue = new Path2D();
      tongue.ellipse(mx, my + smile * 8 + openM * 20, mw * 0.45, 5 + openM * 6, 0, 0, Math.PI);
      ctx.fillStyle = rgba(this._tone(C.pink), 0.6 * faceA * Math.min(1, openM * 2));
      ctx.fill(tongue);
    }
  }

  /* ---------- sparks and overlays ---------- */

  _drawSparks(m) {
    const ctx = this._ctx;
    const cur = this._cur;
    const t = this._t;
    const P = this._pose;
    const count = this._icon ? 12 : this._sparks.length;
    const sp = this._spread;
    for (let i = 0; i < count; i++) {
      const s = this._sparks[i];
      const ang = s.ang + t * s.speed * (0.08 + cur.orbit * 0.6 + cur.wThink * 0.25);
      let x = CENTRE.x + Math.cos(ang) * s.r;
      let y = CENTRE.y + Math.sin(ang) * s.r * 0.92;
      if (cur.wThink > 0.01) {
        const ha = s.ang * 3 + t * (1.6 + s.lane);
        const hr = 205 + s.lane * 40;
        x = lerp(x, HEAD.x + Math.cos(ha) * hr, cur.wThink * (i % 3 === 0 ? 1 : 0.2));
        y = lerp(y, HEAD.y + P.bob + Math.sin(ha) * hr * 0.9, cur.wThink * (i % 3 === 0 ? 1 : 0.2));
      }
      const glyphW = s.square ? cur.wCode : cur.wCode * 0.6;
      if (glyphW > 0.01) {
        const rise = frac(t * (0.12 + s.lane * 0.1) + s.lane);
        const gx = 140 + s.lane * 720 + Math.sin(t + i) * 14;
        const gy = 860 - rise * 760;
        x = lerp(x, gx, glyphW);
        y = lerp(y, gy, glyphW);
      }
      if (cur.wError > 0.01) y += cur.wError * 60;
      x = CENTRE.x + (x - CENTRE.x) * sp;
      y = CENTRE.y + (y - CENTRE.y) * sp;
      const tw = 0.45 + 0.55 * Math.sin(t * s.twSpeed + s.tw);
      const a = clamp(tw, 0, 1) * this._alphaK;
      const col = this._tone(s.c);
      const sz = s.size * (this._icon ? 1.6 : 1);
      if (glyphW < 0.99) {
        ctx.globalAlpha = a * (1 - glyphW);
        ctx.fillStyle = rgba(col, 1);
        if (s.square) {
          ctx.save();
          ctx.translate(x, y);
          ctx.rotate(s.spin + t * 0.8 * m);
          ctx.fillRect(-sz / 2, -sz / 2, sz, sz * 0.7);
          ctx.restore();
        } else {
          ctx.beginPath();
          ctx.arc(x, y, sz * 0.45, 0, TAU);
          ctx.fill();
        }
        if (!this._icon && s.size > 6) this._glow(x, y, sz * 2.4, col, 0.25 * tw * (1 - glyphW));
      }
      if (glyphW > 0.01) {
        ctx.globalAlpha = a * glyphW;
        ctx.font = `700 ${Math.round(18 + s.size * 2.4)}px Consolas, "Cascadia Code", ui-monospace, monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = rgba(mix(col, C.white, 0.25), 1);
        ctx.fillText(s.glyph, x, y);
        ctx.globalAlpha = a * glyphW * 0.35;
        ctx.fillStyle = rgba(col, 1);
        ctx.fillText(s.glyph, x + 1.5, y + 1.5);
      }
    }
    ctx.globalAlpha = 1;
  }

  _drawBurst() {
    const ctx = this._ctx;
    for (const b of this._burst) {
      const life = b.life / b.max;
      ctx.globalAlpha = (1 - life) * this._alphaK;
      ctx.fillStyle = rgba(this._light ? inkOf(b.c) : b.c, 1);
      ctx.save();
      ctx.translate(b.x, b.y);
      ctx.rotate(b.rot);
      ctx.fillRect(-b.s / 2, -b.s / 4, b.s, b.s / 2);
      ctx.restore();
    }
    ctx.globalAlpha = 1;
  }

  _drawOverlays() {
    const alphaK = this._alphaK;
    /* translucent ink washes out on paper, so the state signs stay at full strength in low-energy states */
    if (this._light) this._alphaK = Math.max(alphaK, 1);
    try {
      this._drawSigns();
    } finally {
      this._alphaK = alphaK;
    }
  }

  _drawSigns() {
    const cur = this._cur;
    const P = this._pose;
    const t = this._t;
    if (cur.wThink > 0.01) {
      for (let i = 0; i < 3; i++) {
        const phase = frac(t * 0.8 - i * 0.22);
        const p = new Path2D();
        const x = HEAD.x + 175 + i * 46;
        const y = HEAD.y + P.bob - 175 - i * 46;
        p.arc(x, y, 9 + i * 7, 0, TAU);
        this._neon(p, this._tone(i === 2 ? C.amber : C.cyan), 3, cur.wThink * (0.45 + 0.55 * Math.sin(phase * Math.PI)));
      }
    }
    if (cur.wTalk > 0.01) {
      const a = this._audioS;
      for (let i = 0; i < 3; i++) {
        const p = new Path2D();
        const r = 46 + i * 30;
        const cx = HEAD.x + 12 + 150, cy = HEAD.y + P.bob + 82;
        p.arc(cx - 120, cy, r + 90, -0.42, 0.42);
        this._neon(p, this._tone(i % 2 ? C.magenta : C.cyan), 3, cur.wTalk * clamp(a * 1.6 - i * 0.28, 0, 1));
      }
    }
    if (cur.wPause > 0.01) {
      const x = 740, y = 160;
      for (const dx of [-16, 16]) {
        const p = new Path2D();
        p.moveTo(x + dx, y - 26);
        p.lineTo(x + dx, y + 26);
        this._neon(p, this._tone(C.white), 9, cur.wPause * 0.8);
      }
    }
    if (cur.wApprove > 0.01) {
      const x = HEAD.x + 250, y = HEAD.y + P.bob - 175 + Math.sin(this._ph.wave * 0.5) * 8;
      const bubble = new Path2D();
      bubble.arc(x, y, 46, 0, TAU);
      bubble.moveTo(x - 30, y + 36);
      bubble.lineTo(x - 52, y + 62);
      bubble.lineTo(x - 12, y + 44);
      this._neon(bubble, this._tone(C.amber), 3, cur.wApprove * 0.75);
      const bang = new Path2D();
      bang.moveTo(x, y - 24);
      bang.lineTo(x, y + 6);
      bang.moveTo(x, y + 22);
      bang.lineTo(x, y + 23);
      this._neon(bang, this._tone(C.amber), 10, cur.wApprove * (0.6 + 0.4 * P.amberPulse));
    }
    if (cur.wError > 0.01) {
      const x = HEAD.x + 200, y = HEAD.y + P.bob - 150;
      const p = new Path2D();
      p.moveTo(x - 22, y - 22);
      p.lineTo(x + 22, y + 22);
      p.moveTo(x + 22, y - 22);
      p.lineTo(x - 22, y + 22);
      this._neon(p, this._tone(C.cyan), 6, cur.wError * 0.75);
      const drop = new Path2D();
      const dy = frac(t * 0.5) * 50;
      drop.ellipse(HEAD.x - 175, HEAD.y + P.bob - 40 + dy, 8, 13, 0, 0, TAU);
      this._neon(drop, this._tone(C.cyan), 2.4, cur.wError * (1 - frac(t * 0.5)));
    }
  }
}

if (!customElements.get('jig-avatar')) customElements.define('jig-avatar', JigAvatar);
