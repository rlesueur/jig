/*
 * <jig-avatar-3d> — procedural three.js prototype of the Jig mascot.
 * Everything is built in code: no meshes, textures or rigs are loaded.
 * Mirrors the <jig-avatar> element API (state, task, background, audio-level, framing, reduced-motion, theme).
 */
import * as THREE from './vendor/three.bundle.min.js';

const { EffectComposer, RenderPass, UnrealBloomPass, OutputPass } = THREE;
const V3 = THREE.Vector3;
const TAU = Math.PI * 2;
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const lerp = (a, b, t) => a + (b - a) * t;
const damp = (cur, target, rate, dt) => lerp(cur, target, 1 - Math.exp(-rate * dt));
const smooth = (t) => t * t * (3 - 2 * t);

function hash1(n) {
  const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
}
function noise1(x) {
  const i = Math.floor(x);
  const f = x - i;
  return lerp(hash1(i), hash1(i + 1), smooth(f)) * 2 - 1;
}
/* layered value noise: smooth, irregular, never visibly periodic */
const fbm = (x) => noise1(x) * 0.6 + noise1(x * 2.13 + 7.1) * 0.3 + noise1(x * 4.37 + 3.3) * 0.1;

function seeded(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

class Spring {
  constructor(x = 0, k = 80, d = 10) {
    this.x = x;
    this.v = 0;
    this.k = k;
    this.d = d;
  }
  step(target, dt) {
    this.v += (this.k * (target - this.x) - this.d * this.v) * dt;
    this.x += this.v * dt;
    return this.x;
  }
}

class VSpring {
  constructor(k = 60, d = 11) {
    this.x = new V3();
    this.v = new V3();
    this.k = k;
    this.d = d;
    this._a = new V3();
  }
  step(target, dt) {
    this._a.subVectors(target, this.x).multiplyScalar(this.k).addScaledVector(this.v, -this.d);
    this.v.addScaledVector(this._a, dt);
    this.x.addScaledVector(this.v, dt);
    return this.x;
  }
}

/* ---------- palette (shared with the 2D avatar) ---------- */

export const THEMES = ['dark', 'light'];
const INK = [62, 30, 86];
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
  const s = d === 0 ? 0 : Math.min(1, (d / (1 - Math.abs(2 * L - 1))) * 1.05 + 0.08);
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

function lin(c, k = 1) {
  const col = new THREE.Color().setRGB(c[0] / 255, c[1] / 255, c[2] / 255, THREE.SRGBColorSpace);
  return new V3(col.r * k, col.g * k, col.b * k);
}
const neon = (c, k = 1.6) => lin(c, k);
const ink = (c) => lin(inkOf(c));

/* ---------- states (same names as jig-avatar.js, plus 'listening') ---------- */

export const STATES = ['idle', 'monitoring', 'listening', 'thinking', 'working', 'talking', 'approval', 'success', 'error', 'paused'];
export const TASKS = ['browsing', 'writing', 'coding', 'shopping', 'scheduling'];
const STATE_ALIASES = {
  resting: 'idle', sleeping: 'monitoring', background: 'monitoring', 'needs-approval': 'approval', asking: 'approval',
  call: 'talking', 'on-call': 'talking', blocked: 'error',
};
const TASK_ALIASES = { research: 'browsing', researching: 'browsing', email: 'writing', code: 'coding', payments: 'shopping', payment: 'shopping', calendar: 'scheduling' };

const BASE_POSE = {
  energy: 1, bob: 0.05, swirl: 0.22, wave: 1, bulge: 1, sway: 0.07, lean: 0, tilt: -0.07,
  earUp: 0, eyeOpen: 1, happy: 0, smile: 1, mouthOpen: 0.65, pupil: 0.44,
  hLx: -1.6, hLy: 1.28, hLz: 0.3, hRx: 1.6, hRy: 0.4, hRz: 0.35,
  spreadL: 1, spreadR: 0.85, curlL: 0.12, curlR: 0.3,
  warm: 0, cool: 0, dim: 0, think: 0, approve: 0, browse: 0, write: 0, code: 0,
  orbit: 0.05, timeScale: 1, gazeUp: 0, contact: 0.35, listen: 0,
};
const POSE_KEYS = Object.keys(BASE_POSE);
const STATE_POSES = {
  idle: {},
  monitoring: {
    energy: 0.5, bob: 0.025, swirl: 0.07, wave: 0.5, sway: 0.03, eyeOpen: 0.04, smile: 0.6, mouthOpen: 0, tilt: -0.03,
    lean: 0.14, hLx: -0.75, hLy: 0.15, hLz: 0.45, hRx: 0.75, hRy: 0.1, hRz: 0.45, curlL: 0.5, curlR: 0.5,
    spreadL: 0.5, spreadR: 0.5, earUp: -0.35, dim: 0.35, orbit: 0.02, contact: 0,
  },
  listening: {
    lean: 0.1, tilt: 0.17, earUp: 1, eyeOpen: 1.12, pupil: 0.52, smile: 0.95, mouthOpen: 0.08,
    hLx: -0.95, hLy: 0.55, hLz: 0.7, hRx: 0.95, hRy: 0.5, hRz: 0.7, curlL: 0.35, curlR: 0.35, spreadL: 0.7, spreadR: 0.7,
    contact: 1, swirl: 0.14, bob: 0.03, listen: 1,
  },
  talking: { contact: 1, smile: 1, hLx: -1.25, hLy: 0.7, hLz: 0.5, hRx: 1.3, hRy: 0.55, hRz: 0.5, wave: 1.2, earUp: 0.4 },
  thinking: {
    think: 1, swirl: 0.85, orbit: 0.22, wave: 1.45, tilt: 0.16, gazeUp: 1, smile: 0.35, mouthOpen: 0, contact: 0,
    hRx: 0.34, hRy: 0.82, hRz: 0.85, curlR: 0.75, hLx: -0.45, hLy: 0.25, hLz: 0.8, curlL: 0.5, spreadL: 0.6, earUp: 0.3, eyeOpen: 0.95,
  },
  working: { swirl: 0.4, orbit: 0.1, energy: 1.05, contact: 0 },
  approval: {
    approve: 1, warm: 1, contact: 1, eyeOpen: 1.12, pupil: 0.5, smile: 1.15, mouthOpen: 0.45, tilt: -0.17, lean: 0.06,
    hRx: 1.65, hRy: 1.8, hRz: 0.6, spreadR: 1.3, curlR: 0, hLx: -0.95, hLy: 0.25, hLz: 0.75, curlL: 0.2, bob: 0.09, earUp: 0.65,
  },
  success: {
    happy: 1, smile: 1.4, mouthOpen: 0.75, hLx: -1.75, hLy: 1.75, hLz: 0.6, hRx: 1.75, hRy: 1.75, hRz: 0.6,
    spreadL: 1.3, spreadR: 1.3, curlL: 0, curlR: 0, swirl: 1.0, orbit: 0.3, bob: 0.12, earUp: 1, contact: 1,
  },
  error: {
    cool: 1, eyeOpen: 0.72, smile: -0.7, mouthOpen: 0.05, earUp: -1, tilt: 0.2, lean: 0.2, energy: 0.6,
    hLx: -0.6, hLy: -0.2, hLz: 0.5, hRx: 0.6, hRy: -0.25, hRz: 0.5, curlL: 0.6, curlR: 0.6, swirl: 0.05, wave: 0.4, dim: 0.15, contact: 0.6,
  },
  paused: { timeScale: 0.08, dim: 0.45, eyeOpen: 0.85, smile: 0.5 },
};
const TASK_POSES = {
  browsing: { browse: 1, hLx: -1.0, hLy: 0.2, hLz: 0.55, smile: 0.75, mouthOpen: 0.1 },
  writing: { write: 1, hLx: -0.85, hLy: 0.1, hLz: 0.6, smile: 0.85, mouthOpen: 0.05 },
  coding: { code: 1, smile: 0.7, mouthOpen: 0.25, lean: 0.08 },
  shopping: { browse: 1, warm: 0.5, hLx: -1.0, hLy: 0.2, hLz: 0.55, smile: 1.1 },
  scheduling: { write: 1, hLx: -0.85, hLy: 0.1, hLz: 0.6, smile: 0.85 },
};
const BACKGROUND_SCALE = { energy: 0.62, bob: 0.45, sway: 0.5, swirl: 0.45, orbit: 0.45, wave: 0.6 };

/* ---------- shaders ---------- */

const GLOW_OUT = /* glsl */ `
uniform float uLight;
uniform float uDim;
vec4 glowOut(vec3 neonCol, vec3 inkCol, float a) {
  a = clamp(mix(a, a * 1.9, uLight), 0.0, 1.0) * uDim;
  return vec4(mix(neonCol, inkCol, uLight) * a, a * uLight);
}`;

const HASH = /* glsl */ `
float hash12(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float hash13(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }`;

const FRESNEL_VERT = /* glsl */ `
varying vec3 vN;
varying vec3 vV;
varying vec3 vP;
varying vec2 vUv;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vN = normalize(normalMatrix * normal);
  vV = normalize(-mv.xyz);
  vP = position;
  vUv = uv;
  gl_Position = projectionMatrix * mv;
}`;

const BACKDROP_FRAG = /* glsl */ `
uniform float uLight;
uniform float uTime;
uniform float uAspect;
uniform float uMotion;
varying vec2 vUv;
${HASH}
void main() {
  vec2 p = (vUv - 0.5) * vec2(uAspect, 1.0);
  float r = length(p);
  float ang = atan(p.y, p.x);
  vec3 dark = mix(vec3(0.026, 0.016, 0.07), vec3(0.003, 0.002, 0.01), smoothstep(0.0, 0.85, r));
  float band = pow(0.5 + 0.5 * sin(r * 44.0 - ang * 2.0 + uTime * 0.12 * uMotion), 7.0);
  dark += vec3(0.05, 0.022, 0.13) * band * smoothstep(0.62, 0.15, abs(r - 0.34)) * 0.55;
  vec3 paper = vec3(0.955, 0.887, 0.791) * (1.0 - 0.07 * r * r);
  paper += vec3(0.09, 0.03, 0.06) * band * smoothstep(0.62, 0.15, abs(r - 0.34)) * -0.08;
  paper *= 0.985 + 0.03 * hash12(floor(vUv * 900.0));
  gl_FragColor = vec4(mix(dark, paper, uLight), 1.0);
}`;

const HEAD_FRAG = /* glsl */ `
uniform float uLight;
uniform float uDim;
uniform float uTime;
uniform vec3 uCore, uCoreInk, uRimA, uRimB, uRimAInk, uRimBInk;
varying vec3 vN;
varying vec3 vV;
varying vec3 vP;
${HASH}
void main() {
  vec3 n = normalize(vN);
  float f = 1.0 - max(dot(n, normalize(vV)), 0.0);
  float rim = pow(f, 3.4) * 0.55 + pow(f, 10.0) * 1.5;
  float g = smoothstep(-0.7, 0.9, n.x * 0.55 - n.y * 0.4 + 0.15);
  vec3 rimN = mix(uRimA, uRimB, g);
  vec3 rimI = mix(uRimAInk, uRimBInk, g);
  vec3 cell = floor(vP * 24.0);
  float h = hash13(cell);
  float dotm = smoothstep(0.2, 0.0, length(fract(vP * 24.0) - 0.5)) * step(0.955, h);
  float tw = 0.5 + 0.5 * sin(uTime * (1.0 + h * 3.0) + h * 50.0);
  vec3 dark = uCore + rimN * (pow(f, 1.6) * 0.12 + rim);
  dark += mix(uRimA, uRimB, h) * dotm * tw * (0.2 + f) * 0.7;
  vec3 light = mix(uCoreInk, rimI, clamp(pow(f, 3.0) * 1.0, 0.0, 1.0)) + rimI * dotm * tw * 0.25;
  gl_FragColor = vec4(mix(dark * uDim, light, uLight), 1.0);
}`;

const EAR_FRAG = /* glsl */ `
uniform float uLight;
uniform float uDim;
uniform vec3 uCore, uCoreInk, uGold, uGoldInk, uEdge, uEdgeInk;
varying vec3 vN;
varying vec3 vV;
varying vec2 vUv;
void main() {
  float s = abs(vUv.x);
  float t = vUv.y;
  float f = 1.0 - abs(dot(normalize(vN), normalize(vV)));
  float inner = smoothstep(0.95, 0.15, s) * smoothstep(0.08, 0.45, t) * (1.0 - 0.5 * smoothstep(0.75, 1.0, t));
  float vein = 0.82 + 0.18 * sin(s * 14.0 + t * 6.0);
  float edge = clamp(smoothstep(0.7, 0.98, s) + 0.7 * smoothstep(0.86, 1.0, t) + f * 0.45, 0.0, 1.0);
  float front = gl_FrontFacing ? 1.0 : 0.25;
  float base = smoothstep(0.0, 0.2, t);
  vec3 goldG = mix(vec3(0.55, 0.12, 0.02), uGold, smoothstep(0.1, 0.7, inner)) * mix(0.5, 1.15, pow(inner, 1.4)) * vein;
  goldG *= 1.0 - 0.45 * smoothstep(0.12, 0.0, s) * smoothstep(0.2, 0.5, t);
  vec3 darkC = mix(uCore, goldG, inner * front);
  darkC = mix(darkC, uEdge * 1.0, edge * 0.8);
  darkC = mix(uCore + uEdge * 0.4 * f, darkC, base);
  vec3 lightC = mix(uCoreInk, uGoldInk * mix(0.7, 1.1, inner), inner * front);
  lightC = mix(lightC, uEdgeInk, edge * 0.85);
  lightC = mix(uCoreInk, lightC, base);
  gl_FragColor = vec4(mix(darkC * uDim, lightC, uLight), 1.0);
}`;

const EYE_VERT = /* glsl */ `
varying vec3 vLP;
varying vec3 vVN;
varying vec3 vVV;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vLP = position;
  vVN = normalize(normalMatrix * normal);
  vVV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`;

const EYE_FRAG = /* glsl */ `
uniform float uLight;
uniform float uDim;
uniform float uPupil;
uniform float uTime;
varying vec3 vLP;
varying vec3 vVN;
varying vec3 vVV;
void main() {
  vec3 n = normalize(vLP);
  float r = n.z > 0.0 ? length(n.xy) : 1.0;
  float ir = r / 0.82;
  float ang = atan(n.y, n.x);
  vec3 iris = mix(vec3(0.85, 0.46, 0.1), vec3(0.75, 0.2, 0.03), smoothstep(0.42, 0.86, ir));
  iris *= 0.78 + 0.22 * sin(ang * 21.0 + sin(ang * 5.0) * 2.0) * smoothstep(0.4, 0.8, ir);
  iris += vec3(1.0, 0.42, 0.06) * smoothstep(0.7, 0.86, ir) * smoothstep(0.96, 0.86, ir) * 0.6;
  iris = mix(iris, vec3(0.16, 0.04, 0.03), smoothstep(0.9, 1.0, ir));
  float pup = smoothstep(uPupil + 0.03, uPupil - 0.03, ir);
  vec3 pupilC = mix(vec3(0.05, 0.012, 0.035), vec3(0.22, 0.07, 0.03), smoothstep(uPupil * 0.4, uPupil, ir));
  vec3 col = mix(iris, pupilC, pup);
  col = mix(col, mix(vec3(0.42, 0.12, 0.03), vec3(0.2, 0.05, 0.04), smoothstep(1.1, 1.5, ir)), smoothstep(1.0, 1.06, ir));
  vec3 vn = normalize(vVN);
  float h1 = smoothstep(0.95, 0.968, dot(vn, normalize(vec3(-0.42, 0.52, 0.74))));
  float h2 = smoothstep(0.986, 0.992, dot(vn, normalize(vec3(0.3, -0.26, 0.92))));
  vec3 hl = vec3(1.0, 0.97, 0.95) * (h1 * 1.6 + h2 * 0.9);
  vec3 darkC = col * uDim + hl;
  vec3 lightC = min(col, vec3(1.0)) * vec3(1.0, 0.82, 0.7) + hl * 0.45;
  gl_FragColor = vec4(mix(darkC, lightC, uLight), 1.0);
}`;

const LID_FRAG = /* glsl */ `
uniform float uLight;
uniform float uDim;
uniform float uUpper, uLower, uHappy, uLashK;
uniform vec3 uCore, uCoreInk, uRim, uRimInk, uLash, uLashInk;
varying vec3 vN;
varying vec3 vV;
varying vec3 vP;
void main() {
  vec3 p = normalize(vP);
  if (p.z < -0.15) discard;
  float up = uUpper - 0.3 * p.x * p.x;
  float lo = uLower + 0.2 * p.x * p.x + uHappy * 0.42 * (1.0 - p.x * p.x);
  float dU = p.y - up;
  float dL = lo - p.y;
  float e = max(dU, dL);
  if (e < 0.0) discard;
  float f = 1.0 - max(dot(normalize(vN), normalize(vV)), 0.0);
  float lash = smoothstep(0.1, 0.0, e) * uLashK;
  vec3 darkC = uCore + uRim * pow(f, 3.0) * 0.5 + uLash * lash * 0.7;
  vec3 lightC = mix(uCoreInk, uRimInk, pow(f, 2.4)) + (uLashInk - uCoreInk) * lash * 0.8;
  gl_FragColor = vec4(mix(darkC * uDim, lightC, uLight), 1.0);
}`;

const MOUTH_FRAG = /* glsl */ `
${GLOW_OUT}
uniform float uSmile, uOpen;
uniform vec3 uGold, uGoldInk, uDeep, uDeepInk;
varying vec2 vUv;
void main() {
  float u = vUv.x;
  float v = vUv.y;
  float k = uSmile;
  float top = 0.28 + 0.42 * k * (u * u - 0.25);
  float bot = top - (0.16 + uOpen * 0.95) * pow(max(1.0 - u * u, 0.0), 0.6);
  float d = min(min(v - bot, top - v), 0.86 - abs(u));
  float aa = fwidth(d) * 1.2;
  float a = smoothstep(-aa, aa, d);
  float depth = smoothstep(top, bot, v);
  vec3 n = mix(uGold, uDeep, depth * uOpen);
  vec3 i = mix(uGoldInk, uDeepInk, depth * uOpen);
  gl_FragColor = vec4(mix(n, i, uLight) * a * uDim, a * uDim);
}`;

const TUBE_FRAG = /* glsl */ `
${GLOW_OUT}
uniform vec3 uA, uB, uAInk, uBInk;
uniform float uAlpha;
uniform float uCore;
varying vec3 vN;
varying vec3 vV;
varying vec2 vUv;
void main() {
  float f = 1.0 - abs(dot(normalize(vN), normalize(vV)));
  float a = (uCore + 0.95 * pow(f, 2.2)) * uAlpha;
  vec3 n = mix(uA, uB, vUv.x) * (0.75 + 0.9 * pow(f, 3.0));
  vec3 i = mix(uAInk, uBInk, vUv.x);
  gl_FragColor = glowOut(n, i, a);
}`;

const ORB_FRAG = /* glsl */ `
${GLOW_OUT}
uniform vec3 uA, uAInk;
uniform float uAlpha;
varying vec3 vN;
varying vec3 vV;
void main() {
  float c = max(dot(normalize(vN), normalize(vV)), 0.0);
  float a = pow(c, 1.5) * uAlpha;
  gl_FragColor = glowOut(uA * (1.0 + c), uAInk, a);
}`;

const TORSO_FRAG = /* glsl */ `
${GLOW_OUT}
uniform vec3 uA, uB, uAInk, uBInk;
varying vec3 vN;
varying vec3 vV;
varying vec3 vP;
void main() {
  float f = 1.0 - abs(dot(normalize(vN), normalize(vV)));
  float fade = smoothstep(-0.95, -0.2, vP.y) * smoothstep(0.78, 0.6, vP.y);
  float a = (0.04 + 0.8 * pow(f, 2.8)) * fade;
  float g = smoothstep(-0.8, 0.6, vP.y);
  gl_FragColor = glowOut(mix(uB, uA, g) * (0.7 + pow(f, 3.0)), mix(uBInk, uAInk, g), a);
}`;

const RIBBON_VERT = /* glsl */ `
uniform float uSwirl, uWave, uWaveAmp, uBulge, uSway;
uniform vec3 uTop, uTip, uDrag;
attribute vec2 aSS;
attribute vec4 aSeed;
attribute vec3 aNA, aNB, aIA, aIB;
attribute vec2 aMisc;
varying float vS;
varying float vSide;
varying vec3 vNA, vNB, vIA, vIB;
varying vec2 vMisc;
varying float vSeed;
varying float vFront;
float gTh;
vec3 rib(float s) {
  float env = pow(sin(3.14159 * pow(s, 0.78)), 1.1);
  float R = 0.13 * (1.0 - s) + uBulge * 1.35 * env * aSeed.y;
  float th = aSeed.x + aSeed.z * 6.2832 * s + uSwirl * (0.7 + 0.3 * aSeed.y) + uWaveAmp * 0.45 * sin(s * 6.0 + uWave + aSeed.x * 2.0);
  vec3 c = mix(uTop, uTip, s);
  float bell = sin(3.14159 * s);
  c.x += bell * (0.45 * sin(s * 5.0 - 0.9 + uWave * 0.22) * uWaveAmp + uSway);
  c += uDrag * bell;
  gTh = th;
  return c + vec3(cos(th) * R, 0.2 * sin(th + s * 3.0) * env, sin(th) * R * 0.6);
}
void main() {
  float s = aSS.x;
  vec3 p = rib(s);
  vFront = sin(gTh);
  vec3 tang = normalize(rib(min(s + 0.008, 1.0)) - rib(max(s - 0.008, 0.0)));
  vec3 wp = (modelMatrix * vec4(p, 1.0)).xyz;
  vec3 across = normalize(cross(tang, normalize(cameraPosition - wp)));
  float w = aSeed.w * (0.35 + 0.9 * pow(sin(3.14159 * pow(s, 0.7)), 0.8)) * (1.0 - 0.85 * s * s * s) + 0.003;
  wp += across * aSS.y * w;
  vS = s;
  vSide = aSS.y;
  vNA = aNA; vNB = aNB; vIA = aIA; vIB = aIB;
  vMisc = aMisc;
  vSeed = aSeed.x;
  gl_Position = projectionMatrix * viewMatrix * vec4(wp, 1.0);
}`;

const RIBBON_FRAG = /* glsl */ `
${GLOW_OUT}
uniform float uTime, uMotion, uWarm, uCool;
uniform vec3 uWarmCol, uWarmInk, uCoolCol, uCoolInk;
varying float vS;
varying float vSide;
varying vec3 vNA, vNB, vIA, vIB;
varying vec2 vMisc;
varying float vSeed;
varying float vFront;
void main() {
  float core = exp(-vSide * vSide * mix(2.2, 7.0, uLight)) * (0.55 + 0.45 * smoothstep(1.0, 0.75, abs(vSide)));
  float hot = exp(-vSide * vSide * 26.0) * vMisc.y;
  float fade = smoothstep(0.0, 0.14, vS) * (1.0 - 0.25 * smoothstep(0.88, 1.0, vS));
  float pulse = pow(0.5 + 0.5 * sin(vS * 24.0 - uTime * 2.3 * uMotion + vSeed * 9.0), 6.0);
  float g = smoothstep(0.1, 0.9, vS);
  vec3 n = mix(vNA, vNB, g) * (0.65 + 0.7 * pulse);
  vec3 i = mix(vIA, vIB, g);
  n = mix(n, uWarmCol * (0.7 + 0.6 * pulse), uWarm * 0.45);
  i = mix(i, uWarmInk, uWarm * 0.45);
  n = mix(n, uCoolCol * (0.6 + 0.5 * pulse), uCool * 0.7);
  i = mix(i, uCoolInk, uCool * 0.6);
  n += mix(n, vec3(1.2, 1.1, 1.0), 0.5) * hot * 0.7;
  float a = (core * 0.72 + hot * 0.3) * fade * vMisc.x * (0.45 + 0.55 * smoothstep(-0.8, 0.6, vFront));
  gl_FragColor = glowOut(n, i, a);
}`;

const POOL_FRAG = /* glsl */ `
${GLOW_OUT}
${HASH}
uniform float uTime, uMotion, uLevel;
uniform vec3 uA, uB, uC, uAInk, uBInk, uCInk;
varying vec2 vUv;
void main() {
  vec2 q = (vUv - 0.5) * 6.0;
  float r = length(q);
  float ang = atan(q.y, q.x);
  float rings = 0.0;
  for (int k = 0; k < 4; k++) {
    float rk = fract(uTime * 0.1 * uMotion + float(k) * 0.25) * 2.5;
    rings += exp(-pow((r - rk) * 9.0, 2.0)) * (1.0 - rk / 2.5);
  }
  float fine = pow(0.5 + 0.5 * sin(r * 22.0 - uTime * 1.5 * uMotion + sin(ang * 3.0 + uTime * 0.3) * 0.7), 8.0) * exp(-r * 1.25) * 0.55;
  float centre = (exp(-r * r * 16.0) * 1.5 + exp(-r * r * 2.2) * 0.22) * mix(1.0, 0.45, uLight);
  float fade = smoothstep(2.6, 1.0, r);
  vec2 gq = vec2(ang * 9.0 / 3.14159, r * 9.0);
  float h = hash12(floor(gq));
  float sp = step(0.93, h) * smoothstep(0.28, 0.0, length(fract(gq) - 0.5)) * (0.5 + 0.5 * sin(uTime * 2.0 + h * 30.0));
  float m = smoothstep(0.0, 1.4, r);
  vec3 n = mix(uB, uA, m) * (rings * (0.9 + uLevel) + fine + centre) + uC * sp * 1.3;
  vec3 i = mix(uBInk, uAInk, m);
  float a = (rings * 0.75 + fine + centre) * fade + sp * fade * 0.8;
  gl_FragColor = glowOut(n, mix(i, uCInk, sp), a);
}`;

const LINE_VERT = /* glsl */ `void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const LINE_FRAG = /* glsl */ `
${GLOW_OUT}
uniform vec3 uA, uAInk;
uniform float uAlpha;
void main() { gl_FragColor = glowOut(uA, uAInk, uAlpha); }`;

const SPARK_VERT = /* glsl */ `
uniform float uTime, uOrbit, uPx;
attribute vec4 aOrbit;
attribute vec4 aLook;
attribute vec3 aNeon, aInk;
varying vec3 vNeon, vInk;
varying float vA, vShape, vSpin;
void main() {
  float ang = aOrbit.y + uOrbit * aOrbit.w;
  vec3 p = vec3(cos(ang) * aOrbit.x, aOrbit.z + 0.12 * sin(uTime * 0.4 + aLook.y * 6.28), sin(ang) * aOrbit.x * 0.7 - 0.5);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = aLook.x * uPx / -mv.z;
  vA = 0.5 + 0.5 * sin(uTime * (1.2 + aLook.y * 2.5) + aLook.y * 40.0);
  vNeon = aNeon; vInk = aInk; vShape = aLook.z; vSpin = aLook.w + uTime * 0.6 * (aLook.y - 0.5);
}`;
const SPARK_FRAG = /* glsl */ `
${GLOW_OUT}
varying vec3 vNeon, vInk;
varying float vA, vShape, vSpin;
void main() {
  vec2 q = gl_PointCoord * 2.0 - 1.0;
  float c = cos(vSpin), s = sin(vSpin);
  q = mat2(c, -s, s, c) * q;
  float m;
  if (vShape < 0.5) m = smoothstep(0.62, 0.4, max(abs(q.x), abs(q.y)));
  else if (vShape < 1.5) m = smoothstep(0.85, 0.6, abs(q.x) + abs(q.y));
  else m = exp(-dot(q, q) * 4.0);
  gl_FragColor = glowOut(vNeon, vInk, m * (0.25 + 0.75 * vA));
}`;

const PANEL_FRAG = /* glsl */ `
${GLOW_OUT}
${HASH}
uniform float uMode, uOpacity, uTime, uScroll, uCode, uReadY;
uniform vec2 uPen;
uniform vec3 uFrame, uFrameInk, uText, uTextInk, uAcc, uAccInk, uHi, uHiInk, uMag, uMagInk, uMint, uMintInk;
varying vec2 vUv;
vec3 accN; vec3 accI; float accA;
void lay(vec3 n, vec3 i, float a) {
  a = clamp(a, 0.0, 1.0);
  accN = accN * (1.0 - a) + n * a;
  accI = accI * (1.0 - a) + i * a;
  accA = accA + a * (1.0 - accA);
}
float sdBox(vec2 p, vec2 b, float r) { vec2 q = abs(p) - b + r; return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r; }
float fill(float d) { float aa = fwidth(d); return smoothstep(aa, -aa, d); }
float wig(float x, float row) { return 0.02 * sin(x * 38.0 + row * 1.7) * sin(x * 9.0 + row); }
void main() {
  accN = vec3(0.0); accI = vec3(0.0); accA = 0.0;
  vec2 p = (vUv - 0.5) * vec2(1.6, 1.1);
  float d = sdBox(p, vec2(0.78, 0.53), 0.07);
  lay(uFrame * 0.25, uFrameInk * 0.25 + vec3(0.6), fill(d) * 0.16);
  lay(uFrame * 1.5, uFrameInk, smoothstep(0.012, 0.0, abs(d)) + exp(-max(d, 0.0) * 40.0) * 0.18 * step(0.0, d));
  bool inner = abs(p.x) < 0.7 && abs(p.y) < 0.46;
  if (uMode < 0.5) {
    lay(uAcc, uAccInk, fill(sdBox(p - vec2(-0.12, 0.41), vec2(0.52, 0.03), 0.03)) * 0.8);
    lay(uMag, uMagInk, fill(length(p - vec2(0.6, 0.41)) - 0.03));
    if (p.y < 0.35 && p.y > -0.46) {
      float yy = p.y + uScroll;
      float row = floor(yy / 0.085);
      float fr = fract(yy / 0.085);
      float h = hash12(vec2(row, 3.0));
      float len = 0.35 + 0.6 * hash12(vec2(row, 9.0));
      float heading = step(0.8, h);
      float th = mix(0.012, 0.022, heading);
      float bar = fill(sdBox(vec2(p.x + 0.62 - len * 0.62, (fr - 0.5) * 0.085), vec2(len * 0.62, th), th));
      bool img = mod(row, 7.0) < 1.5;
      if (img) bar = fill(sdBox(vec2(p.x + 0.3, (fr - 0.5) * 0.085), vec2(0.32, 0.036), 0.01)) * 0.7;
      vec3 cn = img ? uHi : (heading > 0.5 ? uMag : uText);
      vec3 ci = img ? uHiInk : (heading > 0.5 ? uMagInk : uTextInk);
      float band = exp(-pow((p.y - uReadY) / 0.04, 2.0));
      lay(uAcc * 0.6, uAccInk, band * 0.22);
      lay(cn * (0.8 + band * 0.8), ci, bar * (0.75 + band * 0.25));
    }
  } else if (uMode < 1.5) {
    float rowF = (0.36 - p.y) / 0.14 + 0.5;
    float row = floor(rowF);
    if (row >= 0.0 && row < 6.0 && abs(p.x) < 0.66) {
      float ry = 0.36 - row * 0.14;
      lay(uFrame * 0.5, uFrameInk, smoothstep(0.004, 0.0, abs(p.y - ry + 0.035)) * 0.3);
      float dy = p.y - (ry + wig(p.x, row));
      float shown = row < uPen.x ? 1.0 : (row == uPen.x ? step(p.x, uPen.y) : 0.0);
      float stroke = smoothstep(0.009, 0.004, abs(dy)) * shown;
      lay(uAcc * 1.2, uAccInk, stroke);
    }
    lay(uHi * 2.0, uHiInk, exp(-dot(p - vec2(uPen.y, 0.36 - uPen.x * 0.14 + wig(uPen.y, uPen.x)), p - vec2(uPen.y, 0.36 - uPen.x * 0.14 + wig(uPen.y, uPen.x))) * 1800.0));
  } else {
    float line = floor((0.42 - p.y) / 0.075);
    float fy = fract((0.42 - p.y) / 0.075);
    if (line >= 0.0 && line < 11.0 && abs(p.x) < 0.7) {
      float indent = floor(hash12(vec2(line, 1.0)) * 3.0) * 0.07;
      float len = 0.25 + 0.6 * hash12(vec2(line, 5.0));
      float x = p.x + 0.64 - indent;
      float seg = floor(x / 0.12);
      float sx = fract(x / 0.12);
      float sl = 0.45 + 0.45 * hash12(vec2(line, seg));
      float on = step(0.0, x) * step(x, len) * step(sx, sl);
      float cur = line - floor(uCode);
      float shown = cur < 0.0 ? 1.0 : (cur == 0.0 ? step(x, fract(uCode) * len) : 0.0);
      float tok = hash12(vec2(seg, line * 3.0));
      vec3 cn = tok < 0.3 ? uMag : (tok < 0.55 ? uHi : (tok < 0.75 ? uMint : uText));
      vec3 ci = tok < 0.3 ? uMagInk : (tok < 0.55 ? uHiInk : (tok < 0.75 ? uMintInk : uTextInk));
      float barMask = smoothstep(0.32, 0.22, abs(fy - 0.5));
      lay(cn, ci, on * shown * barMask);
      float cx = fract(uCode) * len;
      float cursor = (cur == 0.0 ? 1.0 : 0.0) * step(abs(x - cx - 0.012), 0.01) * step(abs(fy - 0.5), 0.36) * step(0.5, fract(uTime * 1.8));
      lay(uAcc * 1.6, uAccInk, cursor);
    }
  }
  float occl = fill(d) * 0.82 * (1.0 - uLight);
  gl_FragColor = vec4(mix(accN, accI, uLight) * accA * uOpacity * uDim, max(accA * uLight, occl) * uOpacity * uDim);
}`;

/* ---------- geometry helpers ---------- */

function glowMaterial(vertexShader, fragmentShader, uniforms, extra = {}) {
  return new THREE.ShaderMaterial({
    vertexShader, fragmentShader, uniforms, transparent: true, depthWrite: false,
    blending: THREE.CustomBlending, blendEquation: THREE.AddEquation,
    blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
    blendSrcAlpha: THREE.OneFactor, blendDstAlpha: THREE.OneMinusSrcAlphaFactor,
    ...extra,
  });
}

/* A tapered tube whose vertices are rewritten every frame from a handful of control points. */
class GlowTube {
  constructor(samples, radial, material) {
    this.samples = samples;
    this.radial = radial;
    const count = samples * (radial + 1);
    this.pos = new Float32Array(count * 3);
    this.nrm = new Float32Array(count * 3);
    const uv = new Float32Array(count * 2);
    const idx = [];
    for (let i = 0; i < samples; i++) {
      for (let j = 0; j <= radial; j++) {
        const k = i * (radial + 1) + j;
        uv[k * 2] = i / (samples - 1);
        uv[k * 2 + 1] = j / radial;
        if (i < samples - 1 && j < radial) {
          const b = k + radial + 1;
          idx.push(k, b, k + 1, b, b + 1, k + 1);
        }
      }
    }
    const g = new THREE.BufferGeometry();
    g.setIndex(idx);
    this.posAttr = new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage);
    this.nrmAttr = new THREE.BufferAttribute(this.nrm, 3).setUsage(THREE.DynamicDrawUsage);
    g.setAttribute('position', this.posAttr);
    g.setAttribute('normal', this.nrmAttr);
    g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    this.mesh = new THREE.Mesh(g, material);
    this.mesh.frustumCulled = false;
    this.curve = new THREE.CatmullRomCurve3([new V3(), new V3(0, 1, 0)], false, 'centripetal');
    this._p = new V3();
    this._t = new V3();
    this._n = new V3();
    this._b = new V3();
    this._d = new V3();
  }
  update(points, r0, r1, toCamera) {
    this.curve.points = points;
    const { samples, radial, pos, nrm } = this;
    for (let i = 0; i < samples; i++) {
      const t = i / (samples - 1);
      this.curve.getPoint(t, this._p);
      this.curve.getTangent(t, this._t);
      this._n.crossVectors(this._t, toCamera);
      if (this._n.lengthSq() < 1e-6) this._n.set(1, 0, 0);
      this._n.normalize();
      this._b.crossVectors(this._t, this._n).normalize();
      const r = lerp(r0, r1, Math.pow(t, 0.8));
      for (let j = 0; j <= radial; j++) {
        const a = (j / radial) * TAU;
        this._d.copy(this._n).multiplyScalar(Math.cos(a)).addScaledVector(this._b, Math.sin(a));
        const k = (i * (radial + 1) + j) * 3;
        pos[k] = this._p.x + this._d.x * r;
        pos[k + 1] = this._p.y + this._d.y * r;
        pos[k + 2] = this._p.z + this._d.z * r;
        nrm[k] = this._d.x;
        nrm[k + 1] = this._d.y;
        nrm[k + 2] = this._d.z;
      }
    }
    this.posAttr.needsUpdate = true;
    this.nrmAttr.needsUpdate = true;
  }
}

/* Crescent leaf used for the horn-ears: a cupped, tapered blade along a cubic Bézier spine. */
function leafGeometry(spine, maxW, cup, segL = 40, segW = 12) {
  const curve = new THREE.CubicBezierCurve(...spine);
  const pos = [];
  const uv = [];
  const idx = [];
  for (let i = 0; i <= segL; i++) {
    const t = i / segL;
    const p = curve.getPoint(t);
    const tan = curve.getTangent(t);
    const nx = -tan.y, ny = tan.x;
    const w = maxW * Math.pow(Math.max(0, Math.sin(Math.PI * (0.16 + 0.84 * t))), 0.85);
    for (let j = 0; j <= segW; j++) {
      const s = -1 + (2 * j) / segW;
      pos.push(p.x + nx * s * w, p.y + ny * s * w, -cup * (1 - s * s) * w + 0.05 * s * s * w);
      uv.push(s, t);
      if (i < segL && j < segW) {
        const a = i * (segW + 1) + j;
        const b = a + segW + 1;
        idx.push(a, b, a + 1, b, b + 1, a + 1);
      }
    }
  }
  const g = new THREE.BufferGeometry();
  g.setIndex(idx);
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.computeVertexNormals();
  return g;
}

function shardGeometry(rnd) {
  const top = new V3((rnd() - 0.5) * 0.3, 1.5 + rnd() * 0.5, (rnd() - 0.5) * 0.2);
  const bot = new V3((rnd() - 0.5) * 0.3, -0.9 - rnd() * 0.4, (rnd() - 0.5) * 0.2);
  const ring = [];
  const n = 4 + (rnd() < 0.4 ? 1 : 0);
  for (let i = 0; i < n; i++) {
    const a = (i / n) * TAU + (rnd() - 0.5) * 0.5;
    const r = 0.45 + rnd() * 0.3;
    ring.push(new V3(Math.cos(a) * r, 0.1 + (rnd() - 0.5) * 0.4, Math.sin(a) * r * 0.55));
  }
  const pos = [];
  for (let i = 0; i < n; i++) {
    const a = ring[i], b = ring[(i + 1) % n];
    pos.push(top.x, top.y, top.z, a.x, a.y, a.z, b.x, b.y, b.z);
    pos.push(bot.x, bot.y, bot.z, b.x, b.y, b.z, a.x, a.y, a.z);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.computeVertexNormals();
  return g;
}

/* ---------- the element ---------- */

const TEMPLATE = `
<style>
  :host { display: inline-block; position: relative; width: 240px; height: 240px; contain: strict; }
  canvas { display: block; width: 100%; height: 100%; }
</style>
<canvas part="canvas"></canvas>`;

const reducedQuery = matchMedia('(prefers-reduced-motion: reduce)');
const FOV = 26;
const HEAD_Y = 1.55;
const TIP = new V3(0.05, -2.3, 0);
const UPPER = 0.85;
const FORE = 0.8;

export class JigAvatar3D extends HTMLElement {
  static get observedAttributes() {
    return ['state', 'task', 'background', 'audio-level', 'framing', 'reduced-motion', 'theme'];
  }

  constructor() {
    super();
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = TEMPLATE;
    this._canvas = root.querySelector('canvas');
    this._state = 'idle';
    this._task = null;
    this._background = false;
    this._target = { ...BASE_POSE };
    this._cur = { ...BASE_POSE };
    this._audio = 0;
    this._built = false;
    this._running = false;
    this.stats = { fps: 0, frameMs: 0, firstRenderMs: null, renderer: '' };
  }

  connectedCallback() {
    if (!this._built) this._build();
    this._ro = new ResizeObserver((e) => this._resize(e[0].contentRect.width, e[0].contentRect.height));
    this._ro.observe(this);
    this._onPointer = (e) => {
      this._pointer.x = e.clientX;
      this._pointer.y = e.clientY;
      this._pointer.at = this._rt;
    };
    window.addEventListener('pointermove', this._onPointer);
    if (!this.hasAttribute('role')) this.setAttribute('role', 'img');
    this._updateLabel();
    this._lightK = this.theme === 'light' ? 1 : 0;
    this._running = true;
    this._last = performance.now();
    this._raf = requestAnimationFrame((t) => this._loop(t));
  }

  disconnectedCallback() {
    this._running = false;
    cancelAnimationFrame(this._raf);
    this._ro.disconnect();
    window.removeEventListener('pointermove', this._onPointer);
  }

  attributeChangedCallback(name, oldValue, value) {
    if (this._reflecting || oldValue === value) return;
    if (name === 'state' || name === 'task' || name === 'background') {
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
    } else if (name === 'theme') {
      if (value !== null && !THEMES.includes(value)) throw new RangeError(`jig-avatar-3d: unknown theme "${value}"`);
    } else if (name === 'framing') {
      if (value !== null && !['auto', 'icon', 'full'].includes(value)) throw new RangeError(`jig-avatar-3d: unknown framing "${value}"`);
      if (this._built) this._frameCamera();
    }
  }

  get theme() {
    return this.getAttribute('theme') === 'light' ? 'light' : 'dark';
  }
  set theme(v) {
    if (!THEMES.includes(v)) throw new RangeError(`jig-avatar-3d: unknown theme "${v}"`);
    this.setAttribute('theme', v);
  }
  get state() {
    return this._state;
  }
  get task() {
    return this._task;
  }
  get canvas() {
    return this._canvas;
  }
  get reducedMotion() {
    const a = this.getAttribute('reduced-motion');
    if (a === 'true' || a === '') return true;
    if (a === 'false') return false;
    return reducedQuery.matches;
  }

  setState(name, options = {}) {
    const state = STATE_ALIASES[name] || name;
    if (!STATES.includes(state)) throw new RangeError(`jig-avatar-3d: unknown state "${name}". Expected one of: ${STATES.join(', ')}`);
    let task = null;
    if (state === 'working') {
      const raw = options.task;
      if (!raw) throw new TypeError('jig-avatar-3d: the "working" state requires a task option');
      task = TASK_ALIASES[raw] || raw;
      if (!TASKS.includes(task)) throw new RangeError(`jig-avatar-3d: unknown task "${raw}". Expected one of: ${TASKS.join(', ')}`);
    } else if (options.task) {
      throw new TypeError(`jig-avatar-3d: the "${state}" state does not take a task`);
    }
    const background = options.background ?? false;
    const changed = state !== this._state || task !== this._task || background !== this._background;
    const previous = this._state;
    this._state = state;
    this._task = task;
    this._background = background;
    const pose = { ...BASE_POSE, ...STATE_POSES[state], ...(task ? TASK_POSES[task] : {}) };
    if (background) {
      for (const k in BACKGROUND_SCALE) pose[k] *= BACKGROUND_SCALE[k];
      pose.eyeOpen = Math.min(pose.eyeOpen, 0.5);
      pose.dim = Math.max(pose.dim, 0.3);
    }
    this._target = pose;
    if (changed && this._built) {
      /* squash-and-stretch kick plus a little hop on every transition */
      const m = this.reducedMotion ? 0.3 : 1;
      this._squash.v += (state === 'success' ? 6 : 3.4) * m;
      this._hop.v += (state === 'success' ? 5 : 1.6) * m;
      this._earL.v += 5 * m;
      this._earR.v -= 5 * m;
      if (Math.random() < 0.6) this._blinkStart = this._rt;
    }
    this._reflecting = true;
    this.setAttribute('state', state);
    if (task) this.setAttribute('task', task);
    else this.removeAttribute('task');
    if (background) this.setAttribute('background', '');
    else this.removeAttribute('background');
    this._reflecting = false;
    this._updateLabel();
    if (changed) this.dispatchEvent(new CustomEvent('jig-statechange', { detail: { state, task, previous, background }, bubbles: true }));
  }

  setAudioLevel(level) {
    if (typeof level !== 'number' || Number.isNaN(level) || level < 0 || level > 1) {
      throw new RangeError(`jig-avatar-3d: audio level must be a number between 0 and 1, received ${level}`);
    }
    this._audio = level;
  }

  _updateLabel() {
    const label = this._task ? `Jig is ${this._state}: ${this._task}` : `Jig is ${this._state}`;
    this.setAttribute('aria-label', this._background ? `${label}, in the background` : label);
  }

  /* ---------- scene construction ---------- */

  _build() {
    this._built = true;
    const renderer = new THREE.WebGLRenderer({ canvas: this._canvas, antialias: false, powerPreference: 'high-performance' });
    const gl = renderer.getContext();
    if (!(gl instanceof WebGL2RenderingContext)) throw new Error('jig-avatar-3d: WebGL2 is required');
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    this.stats.renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    renderer.toneMapping = THREE.NoToneMapping;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer = renderer;

    const scene = new THREE.Scene();
    this.scene = scene;
    this.camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 100);

    this._shared = { uTime: { value: 0 }, uLight: { value: 0 }, uDim: { value: 1 }, uMotion: { value: 1 } };
    const S = this._shared;

    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(this._envScene(), 0.04).texture;
    pmrem.dispose();

    /* backdrop */
    this._backdrop = new THREE.Mesh(
      new THREE.PlaneGeometry(2, 2),
      new THREE.ShaderMaterial({
        vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.9999, 1.0); }',
        fragmentShader: BACKDROP_FRAG,
        uniforms: { uLight: S.uLight, uTime: S.uTime, uMotion: S.uMotion, uAspect: { value: 1 } },
        depthTest: false, depthWrite: false,
      }),
    );
    this._backdrop.frustumCulled = false;
    this._backdrop.renderOrder = -1000;
    scene.add(this._backdrop);

    this._buildCharacter();
    this._buildRibbons();
    this._buildWorld();
    this._buildProps();

    /* post-processing: HDR target with MSAA, bloom, then sRGB output */
    const rt = new THREE.WebGLRenderTarget(4, 4, { type: THREE.HalfFloatType, samples: 4 });
    this.composer = new EffectComposer(renderer, rt);
    this.composer.addPass(new RenderPass(scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(4, 4), 1.0, 0.55, 0.15);
    /* weight the bloom towards the fine mips: tight neon halos, no wide fog over the dark head */
    this.bloom.compositeMaterial.uniforms.bloomFactors.value = [1.0, 0.55, 0.22, 0.07, 0.02];
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    /* behaviour state */
    this._rt = 0;
    this._t = 0;
    this._ph = { swirl: 0, wave: 0, sway: Math.random() * 10, orbit: 0, breath: 0, bob: 0 };
    this._pointer = { x: 0, y: 0, at: -100 };
    this._squash = new Spring(0, 170, 8);
    this._hop = new Spring(0, 90, 9);
    this._earL = new Spring(0, 120, 7);
    this._earR = new Spring(0, 120, 7);
    this._headYaw = new Spring(0, 30, 9);
    this._headPitch = new Spring(0, 30, 9);
    this._headRoll = new Spring(0, 26, 7);
    this._eyeYaw = new Spring(0, 900, 60);
    this._eyePitch = new Spring(0, 900, 60);
    this._handL = new VSpring(55, 10);
    this._handR = new VSpring(55, 10);
    this._handL.x.set(BASE_POSE.hLx, BASE_POSE.hLy, BASE_POSE.hLz);
    this._handR.x.set(BASE_POSE.hRx, BASE_POSE.hRy, BASE_POSE.hRz);
    this._drag = new VSpring(14, 5);
    this._prevBody = new V3();
    this._gaze = { target: new V3(0, 1.4, 10), idle: new V3(0, 1.4, 10), next: 0, micro: new THREE.Vector2(), microNext: 0, glanceUntil: -1 };
    this._blinkStart = -10;
    this._nextBlink = 1.5;
    this._doubleBlink = false;
    this._nextFidget = 3 + Math.random() * 3;
    this._fidget = { wiggle: 0 };
    this._read = { t: 0, dur: 1.3, scroll: 0, scrollTarget: 0 };
    this._pen = { p: 0 };
    this._code = { v: 0, pause: 0, nextTap: 0, taps: new Float32Array(10) };
    this._curls = { L: [0, 0, 0, 0, 0], R: [0, 0, 0, 0, 0] };
    this._light = 0;
    this._motion = 1;
    this._fpsFrames = 0;
    this._fpsT = 0;
  }

  _envScene() {
    const s = new THREE.Scene();
    s.add(new THREE.Mesh(new THREE.SphereGeometry(10, 32, 16), new THREE.MeshBasicMaterial({ color: new THREE.Color(0.02, 0.012, 0.05), side: THREE.BackSide })));
    const panel = (c, x, y, z, w, h) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(...c), side: THREE.DoubleSide }));
      m.position.set(x, y, z);
      m.lookAt(0, 0, 0);
      s.add(m);
    };
    panel([2.2, 0.8, 4.5], -5, 4, 3, 4, 3);
    panel([0.6, 2.6, 3.5], 6, 1, 2, 3, 5);
    panel([4.5, 1.8, 0.4], 0, -5, 4, 6, 2);
    panel([3.5, 0.6, 3.0], 2, 5, -5, 4, 2);
    panel([1.2, 1.2, 1.6], 0, 0, 8, 5, 5);
    return s;
  }

  _buildCharacter() {
    const S = this._shared;
    this.body = new THREE.Group();
    this.scene.add(this.body);

    this.head = new THREE.Group();
    this.body.add(this.head);
    const headUniforms = {
      uLight: S.uLight, uDim: S.uDim, uTime: S.uTime,
      uCore: { value: new V3(0.006, 0.004, 0.02) }, uCoreInk: { value: lin(INK) },
      uRimA: { value: neon(C.violet, 1.1) }, uRimB: { value: neon(C.cyan, 0.9) },
      uRimAInk: { value: ink(C.violet) }, uRimBInk: { value: ink(C.magenta) },
    };
    this.headMesh = new THREE.Mesh(new THREE.SphereGeometry(1, 72, 48), new THREE.ShaderMaterial({ vertexShader: FRESNEL_VERT, fragmentShader: HEAD_FRAG, uniforms: headUniforms }));
    this.headMesh.scale.set(1.07, 0.95, 0.97);
    this.head.add(this.headMesh);

    /* horn-ears: two big crescents on top plus two little side spikes */
    const earUniforms = {
      uLight: S.uLight, uDim: S.uDim,
      uCore: headUniforms.uCore, uCoreInk: headUniforms.uCoreInk,
      uGold: { value: neon(C.amber, 0.95) }, uGoldInk: { value: ink(C.orange) },
      uEdge: { value: neon(C.violet, 1.0) }, uEdgeInk: { value: ink(C.violet) },
    };
    const earMat = new THREE.ShaderMaterial({ vertexShader: FRESNEL_VERT, fragmentShader: EAR_FRAG, uniforms: earUniforms, side: THREE.DoubleSide });
    const bigLeaf = leafGeometry([new THREE.Vector2(0, 0), new THREE.Vector2(-0.42, 0.42), new THREE.Vector2(-0.36, 0.95), new THREE.Vector2(0.1, 1.18)], 0.23, 0.35);
    const smallLeaf = leafGeometry([new THREE.Vector2(0, 0), new THREE.Vector2(-0.12, 0.25), new THREE.Vector2(-0.1, 0.45), new THREE.Vector2(0.06, 0.6)], 0.16, 0.3);
    this.ears = [];
    for (const side of [-1, 1]) {
      const big = new THREE.Group();
      big.position.set(side * 0.5, 0.74, 0.05);
      const bm = new THREE.Mesh(bigLeaf, earMat);
      bm.scale.x = -side;
      bm.position.y = -0.1;
      big.add(bm);
      big.userData = { side, rest: side * -0.18, restX: -0.12, big: true };
      this.head.add(big);
      const small = new THREE.Group();
      small.position.set(side * 0.95, 0.32, 0.05);
      const sm = new THREE.Mesh(smallLeaf, earMat);
      sm.scale.x = -side;
      sm.position.y = -0.06;
      small.add(sm);
      small.userData = { side, rest: side * -0.95, restX: 0, big: false };
      this.head.add(small);
      this.ears.push(big, small);
    }

    /* eyes: sockets carry the lids; the eyeballs rotate inside them */
    this.eyes = [];
    const ER = 0.3;
    for (const side of [-1, 1]) {
      const dir = new V3(side * 0.35, -0.07, 0.93).normalize();
      const socket = new THREE.Group();
      const surf = new V3(dir.x * 1.07, dir.y * 0.95, dir.z * 0.97);
      socket.position.copy(surf).addScaledVector(dir, -ER * 0.42);
      socket.quaternion.setFromUnitVectors(new V3(0, 0, 1), new V3(dir.x * 0.6, dir.y * 0.4, dir.z).normalize());
      this.head.add(socket);
      const ball = new THREE.Mesh(
        new THREE.SphereGeometry(ER, 48, 32),
        new THREE.ShaderMaterial({ vertexShader: EYE_VERT, fragmentShader: EYE_FRAG, uniforms: { uLight: S.uLight, uDim: S.uDim, uTime: S.uTime, uPupil: { value: 0.44 } } }),
      );
      ball.rotation.order = 'YXZ';
      socket.add(ball);
      const lidU = {
        uLight: S.uLight, uDim: S.uDim, uUpper: { value: 0.7 }, uLower: { value: -0.85 }, uHappy: { value: 0 }, uLashK: { value: 0 },
        uCore: headUniforms.uCore, uCoreInk: headUniforms.uCoreInk,
        uRim: { value: neon(C.violet, 1.2) }, uRimInk: { value: ink(C.violet) },
        uLash: { value: neon(C.magenta, 1.3) }, uLashInk: { value: lin([40, 14, 60]) },
      };
      const lid = new THREE.Mesh(new THREE.SphereGeometry(ER * 1.07, 48, 32), new THREE.ShaderMaterial({ vertexShader: FRESNEL_VERT, fragmentShader: LID_FRAG, uniforms: lidU }));
      socket.add(lid);
      this.eyes.push({ side, socket, ball, lid, worldPos: new V3() });
    }

    /* mouth: a patch of the head sphere drawn with a signed-distance smile */
    const mouthDir = new V3(0, -0.43, 0.9).normalize();
    const U = new V3(1, 0, 0);
    const Vt = new V3().crossVectors(mouthDir, U).normalize();
    const mpos = [];
    const muv = [];
    const midx = [];
    const MU = 16, MV = 10;
    for (let i = 0; i <= MV; i++) {
      for (let j = 0; j <= MU; j++) {
        const u = -1 + (2 * j) / MU;
        const v = -1 + (2 * i) / MV;
        const p = mouthDir.clone().addScaledVector(U, u * 0.19).addScaledVector(Vt, v * 0.13).normalize().multiplyScalar(1.004);
        mpos.push(p.x, p.y, p.z);
        muv.push(u, v);
        if (i < MV && j < MU) {
          const a = i * (MU + 1) + j;
          midx.push(a, a + 1, a + MU + 1, a + 1, a + MU + 2, a + MU + 1);
        }
      }
    }
    const mg = new THREE.BufferGeometry();
    mg.setIndex(midx);
    mg.setAttribute('position', new THREE.Float32BufferAttribute(mpos, 3));
    mg.setAttribute('uv', new THREE.Float32BufferAttribute(muv, 2));
    this._mouthU = {
      uLight: S.uLight, uDim: S.uDim, uSmile: { value: 1 }, uOpen: { value: 0.3 },
      uGold: { value: neon(C.amber, 1.5) }, uGoldInk: { value: lin(C.amber, 0.9) },
      uDeep: { value: neon(C.orange, 0.9) }, uDeepInk: { value: lin(C.orange, 0.6) },
    };
    const mouthMat = new THREE.ShaderMaterial({
      vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
      fragmentShader: MOUTH_FRAG, uniforms: this._mouthU, transparent: true, depthWrite: false,
      blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
      polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
    });
    this.mouth = new THREE.Mesh(mg, mouthMat);
    this.headMesh.add(this.mouth);

    /* torso: a slim glowing lathe that dissolves into the ribbons */
    const prof = [[0.001, 0.78], [0.15, 0.7], [0.17, 0.5], [0.21, 0.2], [0.29, -0.15], [0.3, -0.5], [0.2, -0.9]].map(([r, y]) => new THREE.Vector2(r, y));
    this.torso = new THREE.Mesh(
      new THREE.LatheGeometry(prof, 40),
      glowMaterial(FRESNEL_VERT, TORSO_FRAG, {
        uLight: S.uLight, uDim: S.uDim,
        uA: { value: neon(C.violet, 1.3) }, uB: { value: neon(C.cyan, 1.2) }, uAInk: { value: ink(C.violet) }, uBInk: { value: ink(C.blue) },
      }),
    );
    this.body.add(this.torso);

    /* arms and fingers */
    const armMat = glowMaterial(FRESNEL_VERT, TUBE_FRAG, {
      uLight: S.uLight, uDim: S.uDim, uAlpha: { value: 1 }, uCore: { value: 0.22 },
      uA: { value: neon(C.violet, 1.4) }, uB: { value: neon(C.blue, 1.5) }, uAInk: { value: ink(C.violet) }, uBInk: { value: ink(C.blue) },
    });
    const fingerMat = glowMaterial(FRESNEL_VERT, TUBE_FRAG, {
      uLight: S.uLight, uDim: S.uDim, uAlpha: { value: 1 }, uCore: { value: 0.22 },
      uA: { value: neon(C.blue, 1.5) }, uB: { value: neon(C.pink, 1.6) }, uAInk: { value: ink(C.blue) }, uBInk: { value: ink(C.magenta) },
    });
    this.arms = [];
    for (const side of [-1, 1]) {
      const arm = new GlowTube(40, 8, armMat);
      this.scene.add(arm.mesh);
      const fingers = [];
      for (let f = 0; f < 5; f++) {
        const ft = new GlowTube(12, 6, fingerMat);
        this.scene.add(ft.mesh);
        fingers.push(ft);
      }
      this.arms.push({
        side, tube: arm, fingers,
        S: new V3(), E: new V3(), W: new V3(), D: new V3(),
        pts: [new V3(), new V3(), new V3(), new V3(), new V3(), new V3()],
        fpts: Array.from({ length: 5 }, () => [new V3(), new V3(), new V3(), new V3()]),
        tips: Array.from({ length: 5 }, () => new V3()),
      });
    }
  }

  _buildRibbons() {
    const S = this._shared;
    const rnd = seeded(23);
    const SEG = 150;
    const main = [C.violet, C.magenta, C.cyan, C.orange, C.blue, C.amber, C.violet, C.cyan, C.magenta, C.orange, C.blue, C.violet, C.amber, C.pink];
    const defs = [];
    main.forEach((c, i) => defs.push({
      th: (i / main.length) * TAU + (rnd() - 0.5) * 0.4, rs: 0.62 + rnd() * 0.55, tw: 0.42 + rnd() * 0.4,
      w: 0.07 + rnd() * 0.07, a: c, b: main[(i + 5) % main.length], alpha: 0.42, hot: c === C.orange || c === C.amber ? 0.8 : 0,
    }));
    for (let i = 0; i < 22; i++) {
      defs.push({ th: rnd() * TAU, rs: 0.45 + rnd() * 0.85, tw: 0.3 + rnd() * 0.65, w: 0.006 + rnd() * 0.012, a: main[(rnd() * main.length) | 0], b: main[(rnd() * main.length) | 0], alpha: 0.22, hot: 0.5 });
    }
    const vc = defs.length * (SEG + 1) * 2;
    const ss = new Float32Array(vc * 2), seed = new Float32Array(vc * 4), misc = new Float32Array(vc * 2);
    const na = new Float32Array(vc * 3), nb = new Float32Array(vc * 3), ia = new Float32Array(vc * 3), ib = new Float32Array(vc * 3);
    const idx = [];
    let v = 0;
    for (const d of defs) {
      const cols = [neon(d.a, 1.0), neon(d.b, 1.0), ink(d.a), ink(d.b)];
      const base = v;
      for (let i = 0; i <= SEG; i++) {
        for (const side of [-1, 1]) {
          ss.set([i / SEG, side], v * 2);
          seed.set([d.th, d.rs, d.tw, d.w], v * 4);
          misc.set([d.alpha, d.hot], v * 2);
          na.set([cols[0].x, cols[0].y, cols[0].z], v * 3);
          nb.set([cols[1].x, cols[1].y, cols[1].z], v * 3);
          ia.set([cols[2].x, cols[2].y, cols[2].z], v * 3);
          ib.set([cols[3].x, cols[3].y, cols[3].z], v * 3);
          v++;
        }
        if (i < SEG) {
          const a = base + i * 2;
          idx.push(a, a + 2, a + 1, a + 1, a + 2, a + 3);
        }
      }
    }
    const g = new THREE.BufferGeometry();
    g.setIndex(idx);
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(vc * 3), 3));
    g.setAttribute('aSS', new THREE.BufferAttribute(ss, 2));
    g.setAttribute('aSeed', new THREE.BufferAttribute(seed, 4));
    g.setAttribute('aMisc', new THREE.BufferAttribute(misc, 2));
    g.setAttribute('aNA', new THREE.BufferAttribute(na, 3));
    g.setAttribute('aNB', new THREE.BufferAttribute(nb, 3));
    g.setAttribute('aIA', new THREE.BufferAttribute(ia, 3));
    g.setAttribute('aIB', new THREE.BufferAttribute(ib, 3));
    this._ribU = {
      ...S, uSwirl: { value: 0 }, uWave: { value: 0 }, uWaveAmp: { value: 1 }, uBulge: { value: 1 }, uSway: { value: 0 },
      uTop: { value: new V3(0, 0.3, 0) }, uTip: { value: TIP.clone() }, uDrag: { value: new V3() },
      uWarm: { value: 0 }, uCool: { value: 0 },
      uWarmCol: { value: neon(C.amber, 0.85) }, uWarmInk: { value: ink(C.orange) },
      uCoolCol: { value: neon([110, 140, 220], 1.0) }, uCoolInk: { value: ink([90, 110, 180]) },
    };
    this.ribbons = new THREE.Mesh(g, glowMaterial(RIBBON_VERT, RIBBON_FRAG, this._ribU, { side: THREE.DoubleSide }));
    this.ribbons.frustumCulled = false;
    this.scene.add(this.ribbons);
  }

  _buildWorld() {
    const S = this._shared;
    /* glowing pool */
    this._poolU = {
      ...S, uLevel: { value: 0 },
      uA: { value: neon(C.violet, 1.3) }, uB: { value: neon(C.cyan, 1.3) }, uC: { value: neon(C.orange, 1.5) },
      uAInk: { value: ink(C.violet) }, uBInk: { value: ink(C.blue) }, uCInk: { value: ink(C.orange) },
    };
    this.pool = new THREE.Mesh(new THREE.PlaneGeometry(6, 6), glowMaterial('varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }', POOL_FRAG, this._poolU));
    this.pool.rotation.x = -Math.PI / 2;
    this.pool.position.set(TIP.x, TIP.y - 0.1, 0);
    this.scene.add(this.pool);

    /* glass shards */
    const rnd = seeded(7);
    this.shardGroup = new THREE.Group();
    this.scene.add(this.shardGroup);
    this.shards = [];
    const edgeCols = [C.violet, C.cyan, C.magenta, C.orange, C.blue];
    this._glass = [];
    const spots = [[-2.6, 2.3, -0.8], [2.5, 2.4, -1.0], [-3.0, -0.2, -0.4], [2.9, 0.4, -0.6], [-2.4, -1.9, 0.4], [2.6, -1.7, 0.2], [-1.4, 3.3, -1.6], [1.5, 3.3, -1.8], [-3.6, 1.2, -2.0], [3.6, -0.6, -2.2], [-1.9, -2.6, -0.3], [1.8, -2.5, -0.4]];
    spots.forEach((sp, i) => {
      const mat = new THREE.MeshPhysicalMaterial({
        color: new THREE.Color(0.85, 0.8, 1.0), metalness: 0, roughness: 0.06, transmission: 1, thickness: 0.6, ior: 1.45,
        iridescence: 1, iridescenceIOR: 1.35, iridescenceThicknessRange: [120, 700], envMapIntensity: 1.5,
        attenuationColor: new THREE.Color(0.75, 0.55, 1.0), attenuationDistance: 2.5, specularIntensity: 1, transparent: false,
      });
      this._glass.push(mat);
      const geo = shardGeometry(rnd);
      const m = new THREE.Mesh(geo, mat);
      const c = edgeCols[i % edgeCols.length];
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 1), glowMaterial(LINE_VERT, LINE_FRAG, { uLight: S.uLight, uDim: S.uDim, uA: { value: neon(c, 1.6) }, uAInk: { value: ink(c) }, uAlpha: { value: 0.85 } }));
      m.add(edges);
      const size = 0.22 + rnd() * 0.22;
      m.scale.setScalar(size);
      const holder = new THREE.Group();
      holder.add(m);
      this.shardGroup.add(holder);
      this.shards.push({ holder, mesh: m, base: new V3(...sp), ph: rnd() * TAU, spin: (rnd() - 0.5) * 0.5, tumble: 0.2 + rnd() * 0.3, rot0: new THREE.Euler(rnd() * TAU, rnd() * TAU, rnd() * TAU) });
    });
    this._lightA = new THREE.PointLight(new THREE.Color(0.6, 0.35, 1.0), 14, 12, 1.5);
    this._lightA.position.set(0, 0.5, 1.5);
    this._lightB = new THREE.PointLight(new THREE.Color(1.0, 0.6, 0.2), 8, 10, 1.5);
    this._lightB.position.set(0, -2.2, 0.5);
    this.scene.add(this._lightA, this._lightB);

    /* sparkles */
    const N = 300;
    const orbit = new Float32Array(N * 4), look = new Float32Array(N * 4), sn = new Float32Array(N * 3), si = new Float32Array(N * 3);
    const cols = [C.amber, C.magenta, C.cyan, C.violet, C.orange, C.pink, C.blue, C.white];
    const r2 = seeded(11);
    for (let i = 0; i < N; i++) {
      orbit.set([1.1 + Math.pow(r2(), 0.7) * 3.4, r2() * TAU, -2.6 + r2() * 6.2, (0.5 + r2()) * (r2() < 0.5 ? 1 : -1)], i * 4);
      const tiny = r2() < 0.55;
      look.set([tiny ? 0.018 + r2() * 0.014 : 0.035 + r2() * 0.045, r2(), tiny ? 2 : r2() < 0.6 ? 0 : 1, r2() * TAU], i * 4);
      const c = cols[i % cols.length];
      const n = neon(c, 1.3);
      const k = ink(c);
      sn.set([n.x, n.y, n.z], i * 3);
      si.set([k.x, k.y, k.z], i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(N * 3), 3));
    g.setAttribute('aOrbit', new THREE.BufferAttribute(orbit, 4));
    g.setAttribute('aLook', new THREE.BufferAttribute(look, 4));
    g.setAttribute('aNeon', new THREE.BufferAttribute(sn, 3));
    g.setAttribute('aInk', new THREE.BufferAttribute(si, 3));
    this._sparkU = { ...S, uOrbit: { value: 0 }, uPx: { value: 1000 } };
    this.sparks = new THREE.Points(g, glowMaterial(SPARK_VERT, SPARK_FRAG, this._sparkU));
    this.sparks.frustumCulled = false;
    this.scene.add(this.sparks);
  }

  _buildProps() {
    const S = this._shared;
    const vert = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';
    const panelUniforms = (mode) => ({
      ...S, uMode: { value: mode }, uOpacity: { value: 0 }, uScroll: { value: 0 }, uCode: { value: 0 }, uReadY: { value: 0.2 }, uPen: { value: new THREE.Vector2() },
      uFrame: { value: neon(C.cyan, 1.2) }, uFrameInk: { value: ink(C.blue) }, uText: { value: neon(C.violet, 1.1) }, uTextInk: { value: ink(C.violet) },
      uAcc: { value: neon(C.cyan, 1.2) }, uAccInk: { value: ink(C.cyan) }, uHi: { value: neon(C.amber, 1.3) }, uHiInk: { value: ink(C.orange) },
      uMag: { value: neon(C.magenta, 1.3) }, uMagInk: { value: ink(C.magenta) }, uMint: { value: neon(C.mint, 1.1) }, uMintInk: { value: ink([40, 170, 140]) },
    });
    this.panels = {};
    for (const [key, mode] of [['browse', 0], ['write', 1], ['code', 2]]) {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 1.1), glowMaterial(vert, PANEL_FRAG, panelUniforms(mode), { side: THREE.DoubleSide }));
      m.visible = false;
      this.scene.add(m);
      this.panels[key] = m;
    }
    this.panels.browse.position.set(1.62, 0.72, 0.6);
    this.panels.browse.rotation.y = -0.42;
    this.panels.browse.scale.setScalar(0.95);
    this.panels.write.position.set(1.72, 0.7, 0.55);
    this.panels.write.rotation.y = -0.42;
    this.panels.write.scale.setScalar(0.85);
    this.panels.code.position.set(0, -0.12, 1.55);
    this.panels.code.rotation.x = -0.5;
    this.panels.code.scale.setScalar(0.78);

    /* thinking motes */
    const orbMat = (c, a) => glowMaterial(FRESNEL_VERT, ORB_FRAG, { uLight: S.uLight, uDim: S.uDim, uA: { value: neon(c, 2) }, uAInk: { value: ink(c) }, uAlpha: { value: a } });
    this.motes = [C.amber, C.cyan, C.magenta, C.violet].map((c) => {
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.06, 16, 12), orbMat(c, 1));
      m.visible = false;
      this.scene.add(m);
      return m;
    });
    this.penTip = new THREE.Mesh(new THREE.SphereGeometry(0.035, 12, 8), orbMat(C.amber, 1));
    this.penTip.visible = false;
    this.scene.add(this.penTip);

    /* the "asking" question mark */
    const q = new THREE.CatmullRomCurve3([[-0.13, 0.2], [-0.11, 0.31], [0, 0.37], [0.12, 0.31], [0.13, 0.19], [0.04, 0.09], [0.0, -0.01], [0.0, -0.08]].map(([x, y]) => new V3(x, y, 0)));
    const qMat = glowMaterial(FRESNEL_VERT, TUBE_FRAG, {
      uLight: S.uLight, uDim: S.uDim, uAlpha: { value: 0 }, uCore: { value: 0.75 },
      uA: { value: neon(C.amber, 1.7) }, uB: { value: neon(C.orange, 1.6) }, uAInk: { value: ink(C.orange) }, uBInk: { value: ink(C.orange) },
    });
    this.question = new THREE.Group();
    this.question.add(new THREE.Mesh(new THREE.TubeGeometry(q, 48, 0.035, 10, false), qMat));
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.045, 16, 12), qMat);
    dot.position.y = -0.2;
    this.question.add(dot);
    this.question.position.set(-1.35, 2.5, 0.6);
    this.question.visible = false;
    this._qMat = qMat;

    /* listening: sound-wave arcs drifting in towards the ears */
    this.waves = [];
    for (const side of [-1, 1]) {
      for (let i = 0; i < 3; i++) {
        const mat = glowMaterial(FRESNEL_VERT, TUBE_FRAG, {
          uLight: S.uLight, uDim: S.uDim, uAlpha: { value: 0 }, uCore: { value: 0.6 },
          uA: { value: neon(C.cyan, 1.4) }, uB: { value: neon(C.violet, 1.4) }, uAInk: { value: ink(C.blue) }, uBInk: { value: ink(C.violet) },
        });
        const arc = new THREE.Mesh(new THREE.TorusGeometry(1, 0.035, 6, 40, 1.3), mat);
        arc.rotation.z = side < 0 ? Math.PI - 0.65 : -0.65;
        arc.visible = false;
        this.scene.add(arc);
        this.waves.push({ arc, side, i });
      }
    }
    this.scene.add(this.question);
  }

  /* ---------- sizing ---------- */

  _resize(w, h) {
    if (!w || !h) return;
    this._w = w;
    this._h = h;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.renderer.setPixelRatio(dpr);
    this.renderer.setSize(w, h, false);
    this.composer.setPixelRatio(dpr);
    this.composer.setSize(w, h);
    this._backdrop.material.uniforms.uAspect.value = w / h;
    this._frameCamera();
  }

  _frameCamera() {
    if (!this._w) return;
    const aspect = this._w / this._h;
    const mode = this.getAttribute('framing') || 'auto';
    const icon = mode === 'icon' || (mode === 'auto' && Math.min(this._w, this._h) < 150);
    const f = icon ? { cx: 0, cy: 1.75, hx: 1.55, hy: 1.55 } : { cx: 0, cy: 0.45, hx: 2.5, hy: 3.25 };
    const t = Math.tan((FOV * Math.PI) / 360);
    const d = Math.max(f.hy / t, f.hx / (t * aspect));
    this.camera.aspect = aspect;
    this.camera.position.set(f.cx, f.cy + 0.35, d);
    this.camera.lookAt(f.cx, f.cy, 0);
    this.camera.updateProjectionMatrix();
    this._sparkU.uPx.value = this.renderer.domElement.height / (2 * t);
  }

  /* ---------- per-frame ---------- */

  _loop(now) {
    if (!this._running) return;
    this._raf = requestAnimationFrame((t) => this._loop(t));
    const dt = Math.min(0.05, (now - this._last) / 1000);
    this._last = now;
    const t0 = performance.now();
    this._renderFrame(dt);
    this.stats.frameMs = lerp(this.stats.frameMs, performance.now() - t0, 0.1);
    this._fpsFrames++;
    this._fpsT += dt;
    if (this._fpsT >= 0.5) {
      this.stats.fps = this._fpsFrames / this._fpsT;
      this._fpsFrames = 0;
      this._fpsT = 0;
    }
    if (this.stats.firstRenderMs === null) {
      this.stats.firstRenderMs = performance.now();
      this.dispatchEvent(new CustomEvent('jig-firstrender', { detail: { ms: this.stats.firstRenderMs } }));
    }
  }

  /* Renders `frames` frames back-to-back and waits for the GPU each time; returns mean ms per frame. */
  benchmark(frames = 120) {
    const gl = this.renderer.getContext();
    const px = new Uint8Array(4);
    this._renderFrame(1 / 60);
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    const t0 = performance.now();
    for (let i = 0; i < frames; i++) {
      this._renderFrame(1 / 60);
      gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    }
    return (performance.now() - t0) / frames;
  }

  _renderFrame(rdt) {
    if (!this._w) return;
    const P = this._cur;
    const T = this._target;
    for (const k of POSE_KEYS) P[k] = damp(P[k], T[k], 3.2, rdt);

    this._motion = damp(this._motion, this.reducedMotion ? 0.22 : 1, 3, rdt);
    const M = this._motion;
    this._light = damp(this._light, this.theme === 'light' ? 1 : 0, 3.5, rdt);
    const L = smooth(clamp(this._light, 0, 1));

    this._rt += rdt;
    const dt = rdt * P.timeScale;
    this._t += dt;
    const t = this._t;
    const S = this._shared;
    S.uTime.value = t;
    S.uLight.value = L;
    S.uDim.value = 1 - P.dim * 0.55;
    S.uMotion.value = M;

    const ph = this._ph;
    ph.swirl += P.swirl * dt * M;
    ph.wave += (0.9 + 0.5 * P.energy) * dt * (0.4 + 0.6 * M);
    ph.sway += 0.5 * dt * (0.85 + 0.3 * fbm(t * 0.11)) * (0.4 + 0.6 * M);
    ph.orbit += P.orbit * dt * M;
    ph.breath += dt * TAU * 0.24 * (1 + 0.28 * fbm(t * 0.17 + 4));
    ph.bob += dt * TAU * 0.42 * P.energy * (0.5 + 0.5 * M);

    /* breathing is irregular: rate and depth both wander */
    const breath = (Math.sin(ph.breath) + 0.25 * Math.sin(ph.breath * 2 + 0.6)) * (0.8 + 0.35 * fbm(t * 0.23 + 9));
    const bob = Math.sin(ph.bob) * P.bob * (0.5 + 0.5 * M);
    const sway = (Math.sin(ph.sway) * P.sway + fbm(t * 0.3) * 0.04) * (0.4 + 0.6 * M);

    this._fidgets(dt, P);
    const hop = Math.max(0, this._hop.step(0, dt)) * 0.18;
    const sq = this._squash.step(0, dt);

    /* body and head */
    const bodyPos = new V3(sway, bob + hop + breath * 0.02, 0);
    this.body.position.copy(bodyPos);
    const v = this._prevBody.clone().sub(bodyPos).divideScalar(Math.max(dt, 1e-3));
    this._prevBody.copy(bodyPos);
    const drag = this._drag.step(v.multiplyScalar(0.12).clampLength(0, 0.6), dt);
    this.head.position.set(0, HEAD_Y + breath * 0.015, P.lean * 0.4);
    this.head.scale.set(1 - 0.07 * sq, 1 + 0.12 * sq, 1 - 0.07 * sq);
    this.torso.position.set(0, HEAD_Y - 1.32, 0);
    this.torso.scale.set(1 + breath * 0.03, 1, 1 + breath * 0.03);
    this.torso.rotation.z = -sway * 0.3;

    this._updateTasks(dt, P);
    this._updateGaze(rdt, dt, P);

    /* ears: spring follow-through on head motion, perk with attention */
    const headRollVel = this._headRoll.v;
    for (const ear of this.ears) {
      const { side, rest, restX, big } = ear.userData;
      const spring = side < 0 ? this._earL : this._earR;
      const perk = P.earUp * (big ? 0.2 : 0.35);
      if (big) spring.step(0, dt);
      const wob = spring.x * 0.06 - headRollVel * 0.05 + fbm(t * 0.5 + side * 3) * 0.03 * M;
      ear.rotation.z = rest + side * perk + wob * (big ? 1 : 1.6);
      ear.rotation.x = restX - P.earUp * 0.12 + Math.abs(wob) * 0.2;
    }

    /* face */
    const blink = this._updateBlink(t, P);
    const open = clamp(P.eyeOpen, 0, 1.2);
    const pitch = this._eyePitch.x;
    for (const e of this.eyes) {
      const u = e.lid.material.uniforms;
      const upperOpen = lerp(-0.32, 0.9 + (open - 1) * 1.2, clamp(open, 0, 1)) + clamp(pitch, -0.4, 0.2) * 0.2;
      u.uUpper.value = lerp(upperOpen, -0.32, blink);
      u.uLower.value = lerp(-0.98 + P.happy * 0.3, -0.26, Math.max(blink, 1 - clamp(open, 0, 1)));
      u.uHappy.value = P.happy;
      u.uLashK.value = clamp(blink + P.happy + (1 - clamp(open, 0, 1)) * 1.5, 0, 1);
      e.ball.material.uniforms.uPupil.value = P.pupil + 0.04 * fbm(t * 0.4);
    }
    const talk = this._state === 'talking' ? this._audio : 0;
    this._mouthU.uSmile.value = P.smile + 0.08 * fbm(t * 0.3 + 2);
    this._mouthU.uOpen.value = clamp(P.mouthOpen + talk * 0.8, 0, 1);

    this._updateArms(dt, P, bodyPos, t);

    /* ribbons */
    const R = this._ribU;
    R.uSwirl.value = ph.swirl;
    R.uWave.value = ph.wave;
    R.uWaveAmp.value = P.wave * (0.3 + 0.7 * M);
    R.uBulge.value = P.bulge * (1 + breath * 0.025);
    R.uSway.value = sway * 0.6;
    R.uTop.value.set(bodyPos.x * 0.9, HEAD_Y - 1.2 + bodyPos.y, 0);
    R.uTip.value.set(TIP.x + fbm(t * 0.2) * 0.05, TIP.y, 0);
    R.uDrag.value.copy(drag);
    R.uWarm.value = P.warm;
    R.uCool.value = P.cool;

    /* world */
    this._poolU.uLevel.value = this._state === 'listening' || this._state === 'talking' ? this._audio : 0;
    this.shardGroup.rotation.y = Math.sin(ph.orbit * 0.8) * 0.12;
    for (const s of this.shards) {
      s.holder.position.set(s.base.x + Math.cos(ph.orbit * 1.5 + s.ph) * 0.15, s.base.y + Math.sin(t * 0.5 * M + s.ph) * 0.12 + Math.sin(ph.orbit * 1.5 + s.ph) * 0.12, s.base.z);
      s.mesh.rotation.set(s.rot0.x + t * s.tumble * 0.3 * M, s.rot0.y + t * s.spin * M, s.rot0.z);
    }
    this._sparkU.uOrbit.value = ph.orbit * 2.5 + t * 0.03 * M;
    for (const g of this._glass) {
      g.envMapIntensity = lerp(1.5, 0.9, L);
      g.specularIntensity = lerp(1, 0.35, L);
      g.color.setRGB(lerp(0.85, 0.9, L), lerp(0.8, 0.82, L), lerp(1.0, 0.92, L));
    }

    this.bloom.strength = lerp(1.1, 0.15, L) * (1 - P.dim * 0.4);
    this.bloom.threshold = lerp(0.35, 0.97, L);
    this.bloom.radius = 0.0;

    this.composer.render(rdt);
  }

  _fidgets(dt, P) {
    const t = this._t;
    if (t < this._nextFidget) return;
    const M = this.reducedMotion ? 0.35 : 1;
    this._nextFidget = t + (3.5 + Math.random() * 6) / Math.max(0.4, P.energy);
    const pick = Math.random();
    if (pick < 0.25) {
      (Math.random() < 0.5 ? this._earL : this._earR).v += (Math.random() < 0.5 ? 9 : -9) * M;
    } else if (pick < 0.45) {
      this._headRoll.v += (Math.random() - 0.5) * 1.6 * M;
    } else if (pick < 0.62 && P.contact < 0.9 && P.browse + P.write + P.code < 0.3) {
      this._gaze.glanceUntil = t + 0.7 + Math.random() * 0.8;
      this._gaze.idle.set((Math.random() < 0.5 ? -1 : 1) * (3 + Math.random() * 3), 0.5 + Math.random() * 3, 4);
      this._gaze.next = this._gaze.glanceUntil;
    } else if (pick < 0.8) {
      this._fidget.wiggle = 1;
    } else if (pick < 0.92) {
      this._handL.v.y += 1.2 * M;
      this._handR.v.y += 1.2 * M;
      this._squash.v += 1.2 * M;
    } else if (this._state === 'idle') {
      this._hop.v += 2.4 * M;
      this._squash.v += 2 * M;
    }
  }

  _updateBlink(t, P) {
    if (t >= this._nextBlink && P.eyeOpen > 0.15) {
      this._blinkStart = this._rt;
      const focus = P.think + P.code + P.browse * 0.5;
      this._nextBlink = t + (this._doubleBlink ? 0.28 : 1.0 + Math.min(6, -Math.log(1 - Math.random()) * (2.6 + focus * 1.5)));
      this._doubleBlink = !this._doubleBlink && Math.random() < 0.18;
    }
    const e = this._rt - this._blinkStart;
    if (e < 0.065) return smooth(e / 0.065);
    if (e < 0.1) return 1;
    if (e < 0.24) return 1 - smooth((e - 0.1) / 0.14);
    return 0;
  }

  _updateTasks(dt, P) {
    const show = (m, w) => {
      m.visible = w > 0.01;
      m.material.uniforms.uOpacity.value = smooth(clamp(w, 0, 1));
    };
    show(this.panels.browse, P.browse);
    show(this.panels.write, P.write);
    show(this.panels.code, P.code);
    const loose = (this.reducedMotion ? 0.3 : 1);
    for (const key of ['browse', 'write']) {
      this.panels[key].position.y = 0.72 + Math.sin(this._t * 0.7) * 0.03 * loose;
    }

    /* reading: fixations step along a line, then a return saccade and the page scrolls */
    const r = this._read;
    r.t += dt;
    if (r.t > r.dur) {
      r.t = 0;
      r.dur = 1.0 + Math.random() * 0.9;
      r.scrollTarget += 0.085;
      if (Math.random() < 0.3) this._blinkStart = this._rt;
    }
    r.scroll = damp(r.scroll, r.scrollTarget, 6, dt);
    const fix = Math.floor((r.t / r.dur) * 6) / 6;
    r.x = -0.6 + 1.1 * fix;
    r.y = 0.12;
    const ub = this.panels.browse.material.uniforms;
    ub.uScroll.value = r.scroll;
    ub.uReadY.value = r.y;

    /* writing: pen sweeps rows with a handwriting wiggle */
    const pen = this._pen;
    pen.p += dt / 2.4;
    const row = Math.floor(pen.p) % 6;
    const fx = pen.p - Math.floor(pen.p);
    pen.row = row;
    pen.x = -0.64 + 1.28 * fx;
    pen.y = 0.36 - row * 0.14 + 0.02 * Math.sin(pen.x * 38 + row * 1.7) * Math.sin(pen.x * 9 + row);
    this.panels.write.material.uniforms.uPen.value.set(row, pen.x);

    /* coding: irregular bursts of key taps advance the code */
    const c = this._code;
    if (P.code > 0.3) {
      if (c.pause > 0) c.pause -= dt;
      else if (this._t > c.nextTap) {
        c.nextTap = this._t + 0.06 + Math.random() * 0.16;
        c.taps[(Math.random() * 10) | 0] = 1;
        const before = Math.floor(c.v);
        c.v += 0.05 + Math.random() * 0.04;
        if (Math.floor(c.v) !== before && Math.random() < 0.3) c.pause = 0.5 + Math.random() * 1.0;
        if (c.v >= 11) c.v = 0;
      }
    }
    for (let i = 0; i < 10; i++) c.taps[i] = Math.max(0, c.taps[i] - dt * 7);
    this.panels.code.material.uniforms.uCode.value = c.v;

    /* thinking motes and the asking glyph */
    const th = P.think;
    this.motes.forEach((m, i) => {
      m.visible = th > 0.02;
      const a = this._t * (0.9 + i * 0.15) * (0.3 + 0.7 * this._motion) + (i * TAU) / this.motes.length;
      m.position.set(Math.cos(a) * 1.45, HEAD_Y + 0.9 + Math.sin(a * 2) * 0.12 + this.body.position.y, Math.sin(a) * 0.7 + 0.2);
      m.scale.setScalar(Math.max(0.001, th) * (0.8 + 0.3 * Math.sin(this._t * 3 + i)));
    });
    for (const w of this.waves) {
      const k = (this._t * 0.55 + w.i / 3) % 1;
      w.arc.visible = P.listen > 0.02;
      const r = lerp(0.62, 0.22, k);
      w.arc.scale.setScalar(r);
      w.arc.position.set(w.side * (1.18 + r * 0.55) + this.body.position.x, HEAD_Y + 0.1 + this.body.position.y, 0.3);
      w.arc.material.uniforms.uAlpha.value = P.listen * Math.sin(Math.PI * k) * (0.6 + this._audio * 0.6);
    }
    this.question.visible = P.approve > 0.02;
    this._qMat.uniforms.uAlpha.value = P.approve;
    this.question.position.y = 2.5 + Math.sin(this._t * 2.2) * 0.06 * loose;
    this.question.rotation.z = Math.sin(this._t * 1.3) * 0.12 * loose;
    this.question.scale.setScalar((0.6 + 0.4 * P.approve) * 1.35 + 0.06 * Math.sin(this._t * 4));
  }

  _taskGaze(P, out) {
    const w = { browse: P.browse, write: P.write, code: P.code };
    const best = Object.keys(w).reduce((a, b) => (w[a] > w[b] ? a : b));
    if (w[best] < 0.45) return false;
    if (best === 'browse') this.panels.browse.localToWorld(out.set(this._read.x * 0.8, this._read.y, 0));
    else if (best === 'write') this.panels.write.localToWorld(out.set(this._pen.x + 0.05, this._pen.y, 0));
    else {
      if (this._code.pause > 0.2) return false;
      const line = Math.floor(this._code.v);
      const lineY = 0.42 - (line + 0.5) * 0.075;
      this.panels.code.localToWorld(out.set(-0.5 + (this._code.v % 1) * 0.6, lineY, 0));
    }
    return true;
  }

  _updateGaze(rdt, dt, P) {
    const g = this._gaze;
    const t = this._t;
    const rt = this._rt;
    const want = new V3();
    const pointerActive = rt - this._pointer.at < 2.5;
    if (this._taskGaze(P, want)) {
      /* task gaze already set */
    } else if (pointerActive && P.eyeOpen > 0.2) {
      const rect = this.getBoundingClientRect();
      const nx = clamp(((this._pointer.x - rect.left) / rect.width) * 2 - 1, -1.8, 1.8);
      const ny = clamp(-(((this._pointer.y - rect.top) / rect.height) * 2 - 1), -1.8, 1.8);
      const ray = new THREE.Raycaster();
      ray.setFromCamera(new THREE.Vector2(nx, ny), this.camera);
      const plane = new THREE.Plane(new V3(0, 0, 1), -3);
      ray.ray.intersectPlane(plane, want);
    } else {
      if (t > g.next) {
        g.next = t + 0.6 + Math.random() * 2.6;
        if (P.gazeUp > 0.5) g.idle.set(1.2 + Math.random() * 2.2, 3.6 + Math.random() * 1.5, 4);
        else if (Math.random() < 0.35 + P.contact * 0.6) g.idle.copy(this.camera.position).add(new V3((Math.random() - 0.5) * 0.6, (Math.random() - 0.5) * 0.4, 0));
        else g.idle.set((Math.random() - 0.5) * 6, 0.3 + Math.random() * 2.6, 4 + Math.random() * 2);
      }
      want.copy(g.idle);
    }
    if (t > g.microNext) {
      g.microNext = t + 0.25 + Math.random() * 0.7;
      g.micro.set((Math.random() - 0.5) * 0.05, (Math.random() - 0.5) * 0.04);
    }

    /* head turns part-way towards the gaze; eyes do the rest with fast saccades */
    this.head.updateMatrixWorld(true);
    const headWorld = this.head.getWorldPosition(new V3());
    const toT = want.clone().sub(headWorld);
    const yaw = Math.atan2(toT.x, toT.z);
    const pitch = Math.atan2(toT.y, Math.hypot(toT.x, toT.z));
    const M = this._motion;
    const tilt = P.tilt + fbm(t * 0.21 + 5) * 0.05 * M;
    this.head.rotation.order = 'YXZ';
    this.head.rotation.y = this._headYaw.step(clamp(yaw * 0.38, -0.45, 0.45) + fbm(t * 0.13) * 0.05 * M, rdt);
    this.head.rotation.x = this._headPitch.step(clamp(-pitch * 0.32, -0.3, 0.3) + P.lean, rdt);
    this.head.rotation.z = this._headRoll.step(tilt, rdt);
    this.head.updateMatrixWorld(true);

    const local = this.head.worldToLocal(want.clone());
    const e0 = this.eyes[0].socket.position;
    const ly = Math.atan2(local.x, local.z - e0.z);
    const lp = Math.atan2(local.y - e0.y, Math.hypot(local.x, local.z - e0.z));
    const ey = clamp(ly, -0.6, 0.6) + g.micro.x;
    const ep = clamp(lp, -0.45, 0.45) + g.micro.y;
    if (Math.abs(ey - this._eyeYaw.x) > 0.35 && Math.random() < 0.35 && this._rt - this._blinkStart > 0.6) this._blinkStart = this._rt;
    this._eyeYaw.step(ey, rdt);
    this._eyePitch.step(ep, rdt);
    for (const e of this.eyes) {
      /* the shared spring gives saccade timing; each eye then aims from its own socket so near targets converge */
      const sl = e.socket.worldToLocal(want.clone());
      const yy = Math.atan2(sl.x, sl.z);
      const pp = Math.atan2(sl.y, Math.hypot(sl.x, sl.z));
      e.ball.rotation.y = clamp(this._eyeYaw.x * 0.75 + (yy - ly), -0.6, 0.6);
      e.ball.rotation.x = -clamp(this._eyePitch.x * 0.75 + (pp - lp), -0.35, 0.35);
    }
  }

  _updateArms(dt, P, bodyPos, t) {
    const M = this._motion;
    const cam = this.camera.position;
    const tgtL = new V3(P.hLx, P.hLy, P.hLz);
    const tgtR = new V3(P.hRx, P.hRy, P.hRz);
    const tmp = new V3();
    const shoulderY = HEAD_Y - 0.98 + bodyPos.y;
    const SL = new V3(-0.2 + bodyPos.x, shoulderY, 0.05);
    const SR = new V3(0.2 + bodyPos.x, shoulderY, 0.05);
    const reach = (shoulder, tip, len) => tip.clone().sub(tmp.copy(tip).sub(shoulder).normalize().multiplyScalar(len));

    /* task-driven hand targets blend over the state pose */
    if (P.browse > 0.01) {
      const tip = this.panels.browse.localToWorld(new V3(-0.62, this._read.y - 0.04, 0.02));
      tgtR.lerp(reach(SR, tip, 0.44), P.browse);
    }
    if (P.write > 0.01) {
      const tip = this.panels.write.localToWorld(new V3(this._pen.x, this._pen.y, 0.02));
      this.penTip.visible = true;
      this.penTip.position.copy(tip);
      this.penTip.scale.setScalar(P.write);
      tgtR.lerp(reach(SR, tip, 0.36), P.write);
    } else this.penTip.visible = false;
    if (P.code > 0.01) {
      const kl = this.panels.code.localToWorld(new V3(-0.42, -0.42, 0.03));
      const kr = this.panels.code.localToWorld(new V3(0.42, -0.42, 0.03));
      tgtL.lerp(reach(SL, kl, 0.3), P.code);
      tgtR.lerp(reach(SR, kr, 0.3), P.code);
    }

    const drift = (s) => new V3(fbm(t * 0.37 + s) * 0.07, fbm(t * 0.31 + s + 5) * 0.07, fbm(t * 0.29 + s + 9) * 0.04).multiplyScalar(M * P.energy);
    tgtL.add(bodyPos).add(drift(1));
    tgtR.add(bodyPos).add(drift(20));
    const hands = [this._handL.step(tgtL, dt), this._handR.step(tgtR, dt)];
    const toCam = new V3();

    this.arms.forEach((arm, i) => {
      const side = arm.side;
      arm.S.copy(i === 0 ? SL : SR);
      const T = hands[i];
      /* two-bone IK with the elbow pole pointing down and out */
      const pole = new V3(side * 1, -1, -0.35).normalize();
      const d = tmp.subVectors(T, arm.S);
      const dist = clamp(d.length(), 0.25, (UPPER + FORE) * 0.995);
      const dir = d.normalize().clone();
      arm.W.copy(arm.S).addScaledVector(dir, dist);
      const cosA = clamp((UPPER * UPPER + dist * dist - FORE * FORE) / (2 * UPPER * dist), -1, 1);
      const sinA = Math.sqrt(1 - cosA * cosA);
      const perp = pole.clone().addScaledVector(dir, -pole.dot(dir)).normalize();
      arm.E.copy(arm.S).addScaledVector(dir, UPPER * cosA).addScaledVector(perp, UPPER * sinA);
      arm.D.subVectors(arm.W, arm.E).normalize();

      const wob = (k) => fbm(t * 0.8 + k + i * 7) * 0.05 * M;
      const p = arm.pts;
      p[0].copy(arm.S).add(new V3(-side * 0.08, 0.12, -0.02));
      p[1].copy(arm.S);
      p[2].lerpVectors(arm.S, arm.E, 0.5).add(new V3(wob(1), wob(2), 0));
      p[3].copy(arm.E);
      p[4].lerpVectors(arm.E, arm.W, 0.5).add(new V3(wob(3), wob(4), 0));
      p[5].copy(arm.W).addScaledVector(arm.D, 0.08);
      toCam.subVectors(cam, arm.E).normalize();
      arm.tube.update(p, 0.055, 0.026, toCam);

      /* fingers fan out in the plane facing the camera */
      const sideV = new V3().crossVectors(arm.D, toCam).normalize();
      const palmN = new V3().crossVectors(sideV, arm.D).normalize();
      const spread = i === 0 ? P.spreadL : P.spreadR;
      const curlBase = i === 0 ? P.curlL : P.curlR;
      const curls = this._fingerCurls(i, P, curlBase, t);
      const palm = arm.W.clone().addScaledVector(arm.D, 0.08);
      for (let f = 0; f < 5; f++) {
        const thumb = f === 0;
        const idx = thumb ? -2.6 : f - 2.5;
        const ang = idx * 0.28 * spread * side + (thumb ? -0.25 * side : 0);
        const len = thumb ? 0.2 : [0, 0.34, 0.37, 0.33, 0.27][f];
        const fd = arm.D.clone().multiplyScalar(Math.cos(ang)).addScaledVector(sideV, Math.sin(ang));
        const fp = arm.fpts[f];
        fp[0].copy(palm).addScaledVector(sideV, idx * 0.018);
        const cur = new V3().copy(fp[0]);
        let dirF = fd.clone();
        for (let j = 1; j < 4; j++) {
          const bend = curls[f] * 0.75;
          dirF.addScaledVector(palmN, -Math.sin(bend) * 0.9).addScaledVector(arm.D, -Math.sin(bend) * 0.3 * j).normalize();
          dirF.addScaledVector(sideV, -idx * 0.04 * curls[f]).normalize();
          cur.addScaledVector(dirF, len / 3);
          fp[j].copy(cur);
        }
        arm.tips[f].copy(cur);
        arm.fingers[f].update(fp, thumb ? 0.024 : 0.021, 0.006, toCam);
      }
    });
  }

  _fingerCurls(hand, P, base, t) {
    const out = hand === 0 ? this._curls.L : this._curls.R;
    const wig = this._fidget.wiggle;
    this._fidget.wiggle = Math.max(0, wig - 0.004);
    for (let f = 0; f < 5; f++) {
      let c = base + fbm(t * 0.6 + f * 3.1 + hand * 11) * 0.12 + wig * 0.35 * Math.sin(t * 14 - f * 1.2);
      if (hand === 1 && P.browse > 0.01) c = lerp(c, [0.6, 0.02, 0.85, 0.9, 0.9][f], P.browse);
      if (hand === 1 && P.write > 0.01) c = lerp(c, [0.5, 0.35, 0.55, 0.8, 0.85][f], P.write);
      if (P.code > 0.01) c = lerp(c, 0.5 + this._code.taps[hand * 5 + f] * 0.5, P.code);
      out[f] = clamp(c, -0.1, 1.2);
    }
    return out;
  }
}

customElements.define('jig-avatar-3d', JigAvatar3D);
