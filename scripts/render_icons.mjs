// Renders Jig's static icons (favicons, app icons, the sign-in and "Jig is off" faces, the landing-page
// favicons) and the pictures in avatar/screenshots/ from the real <jig-avatar> rig in avatar/jig-avatar.js,
// in headless Chromium, on the manual clock with a fixed seed, so every run gives the same pixels.
//
//   node scripts/render_icons.mjs [--out-dir DIR]
//
// Without --out-dir the files are written in place in the repository. Playwright comes from demos/
// (run "npm ci" there once).
//
// Each icon is Jig idle, calm, eyes open and looking straight out, drawn with the rig's small-icon framing
// on a transparent background, then cropped to a circle around the head (rig units, the same coordinates
// as HEAD in jig-avatar.js). The crop fades out towards its edge, so ribbons and arms trail off instead of
// being cut. Dark icons sit on the rig's own night-sky background; light icons stay transparent.
import { createServer } from 'node:http';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const requireFromDemos = createRequire(path.join(ROOT, 'demos', 'package.json'));
let chromium;
try {
  ({ chromium } = requireFromDemos('playwright'));
} catch (err) {
  throw new Error(`Playwright was not found in demos/node_modules. Run "npm ci" in demos/ first. (${err.message})`);
}

const args = process.argv.slice(2);
const outIdx = args.indexOf('--out-dir');
if (outIdx >= 0 && !args[outIdx + 1]) throw new Error('--out-dir needs a directory');
const OUT = outIdx >= 0 ? path.resolve(args[outIdx + 1]) : ROOT;

const SEED = 7;
/* Gaze straight out: Jig's eye line (HEAD.x, HEAD.y + 30) as a fraction of the icon render's box (icon view
 * centred on 505,405 and 650 wide, in a box 1.3 times as tall as it is wide) and of the full view (centre
 * 500,500, 1000 wide, square). */
const LOOK_AT = '0.5,0.433';
const LOOK_AT_FULL = '0.505,0.348';
const WARM_UP_STEPS = 240; /* four seconds at 60 Hz before looking for a calm frame */

/* Crops in rig units. FULL shows the whole leaf ears, the shoulders and the start of the arms; HEAD keeps
 * only the head, ears and eyes, for sizes where anything more turns to noise. */
const FULL = { cx: 505, cy: 322, r: 300, fade: 0.82 };
const HEAD = { cx: 505, cy: 300, r: 235, fade: 0.86 };

/* bg: 'circle' (the dark porthole badge), 'square' (opaque, for platforms that apply their own mask) or
 * 'none'. inset: the crop circle's radius as a fraction of half the image (the maskable safe zone is 0.8).
 * srcCss: the width of the avatar element, at device pixel ratio 2; the rig picks line widths for a box that
 * size, so small icons are drawn small to keep their lines bold. */
const PNGS = [
  { file: 'jig/web/icons/icon-16.png', px: 16, theme: 'dark', bg: 'circle', crop: HEAD, srcCss: 24 },
  { file: 'jig/web/icons/icon-32.png', px: 32, theme: 'dark', bg: 'circle', crop: HEAD, srcCss: 40 },
  { file: 'jig/web/icons/icon-48.png', px: 48, theme: 'dark', bg: 'circle', crop: HEAD, srcCss: 56 },
  { file: 'jig/web/icons/icon-192.png', px: 192, theme: 'dark', bg: 'circle', crop: FULL, srcCss: 128 },
  { file: 'jig/web/icons/icon-512.png', px: 512, theme: 'dark', bg: 'circle', crop: FULL, srcCss: 320 },
  { file: 'jig/web/icons/apple-touch-icon.png', px: 180, theme: 'dark', bg: 'square', crop: FULL, srcCss: 128 },
  { file: 'jig/web/icons/icon-maskable-512.png', px: 512, theme: 'dark', bg: 'square', crop: FULL, inset: 0.8, srcCss: 320 },
  { file: 'jig/web/jig-face-dark.png', px: 192, theme: 'dark', bg: 'circle', crop: FULL, srcCss: 128 },
  { file: 'jig/web/jig-face-light.png', px: 192, theme: 'light', bg: 'none', crop: FULL, srcCss: 128 },
  { file: 'site/media/favicon.png', px: 64, theme: 'dark', bg: 'circle', crop: HEAD, srcCss: 64 },
  { file: 'site/media/apple-touch-icon.png', px: 180, theme: 'dark', bg: 'square', crop: FULL, srcCss: 128 },
];
const ICO = { file: 'jig/web/favicon.ico', from: ['jig/web/icons/icon-16.png', 'jig/web/icons/icon-32.png', 'jig/web/icons/icon-48.png'] };
/* The picture at the top of README.md: the whole figure, as the earlier screenshot was. */
const README_PICTURE = { file: 'avatar/screenshots/jig-idle.png', px: 520, theme: 'dark' };
/* The state pictures in avatar/screenshots/: whole figure, dark, 520px. Each starts idle, switches state after
 * one second (as in real use) and is captured `at` seconds later, on the first frame after that with eyes
 * not mid-blink. */
const STATE_SHOTS = [
  { file: 'avatar/screenshots/jig-monitoring.png', state: 'monitoring', at: 3 },
  { file: 'avatar/screenshots/jig-working-browsing.png', state: 'working', task: 'browsing', at: 3 },
  { file: 'avatar/screenshots/jig-working-writing.png', state: 'working', task: 'writing', at: 3.5 },
  { file: 'avatar/screenshots/jig-needs-approval.png', state: 'approval', at: 2.5 },
  { file: 'avatar/screenshots/jig-success-pirouette.png', state: 'success', at: 3.6 },
  { file: 'avatar/screenshots/jig-error-blocked.png', state: 'error', at: 3 },
];
/* The 48px icon strip: the demo page's icon grid (avatar/index.html), 3 across, at 277 x 342. */
const ICON_STRIP = {
  file: 'avatar/screenshots/jig-icons-48px.png', width: 277, height: 342, at: 3,
  icons: [['Live', 'idle'], ['Idle', 'idle'], ['Monitoring', 'monitoring'], ['Thinking', 'thinking'],
    ['Research', 'working', 'browsing'], ['Email', 'working', 'writing'], ['Coding', 'working', 'coding'],
    ['Paying', 'working', 'shopping'], ['Calendar', 'working', 'scheduling'], ['Approval', 'approval'],
    ['Success', 'success'], ['Blocked', 'error']],
};
/* GET /favicon.ico serves this SVG (jig/api/app.py). The rig is drawn on a canvas, so the SVG wraps the
 * 48px render rather than tracing it. */
const SVG = { file: 'jig/web/favicon.svg', from: 'jig/web/icons/icon-48.png' };

const PAGE = `<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;background:transparent}jig-avatar{display:block}</style></head><body>
<script type="module">
import '/avatar/jig-avatar.js';
window.jigReady = customElements.whenDefined('jig-avatar').then(() => true);
</script></body></html>`;

function serve() {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/') {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      res.end(PAGE);
      return;
    }
    if (url.pathname === '/avatar/jig-avatar.js') {
      res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8' });
      res.end(await readFile(path.join(ROOT, 'avatar', 'jig-avatar.js')));
      return;
    }
    res.writeHead(404);
    res.end();
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

/* In the page. Makes an avatar `css` wide and 1.3 times as tall (so the ears have headroom; the rig scales
 * to the narrower side), steps it and either finds a calm frame (probe) or keeps that frame for finish().
 * `full` draws the whole figure in its rounded square instead, as the README picture. */
async function renderInPage({ theme, css, steps, lookAt, seed, probe, full }) {
  const h = full ? css : Math.round(css * 1.3);
  const el = document.createElement('jig-avatar');
  for (const [k, v] of Object.entries({
    state: 'idle', theme, shape: full ? 'rounded' : 'none', framing: full ? 'full' : 'icon', clock: 'manual',
    seed: String(seed), 'look-at': lookAt,
    'reduced-motion': '', style: `width:${css}px;height:${h}px`,
  })) el.setAttribute(k, v);
  document.body.append(el);
  const canvas = el.shadowRoot.querySelector('canvas');
  for (let i = 0; i < 120 && canvas.width !== css * 2; i++) await new Promise(requestAnimationFrame);
  if (canvas.width !== css * 2 || canvas.height !== h * 2) {
    throw new Error(`the avatar canvas is ${canvas.width}x${canvas.height}, expected ${css * 2}x${h * 2}`);
  }
  const pose = () => {
    if (!el._sp || !el._sp.gazeX || typeof el._blink !== 'number') {
      throw new Error('jig-avatar no longer exposes _blink and _sp.gaze*, which this script uses to pick an eyes-open frame');
    }
    return { blink: el._blink, gx: el._sp.gazeX.x, gy: el._sp.gazeY.x, vx: el._sp.gazeX.v, vy: el._sp.gazeY.v };
  };
  const calm = (p) => p.blink === 0 && Math.abs(p.gx) < 0.03 && Math.abs(p.gy) < 0.04 && Math.abs(p.vx) < 0.05 && Math.abs(p.vy) < 0.05;
  if (probe) {
    let found = -1;
    for (let i = 0; i < probe.warmUp; i++) el.step(1 / 60);
    for (let i = probe.warmUp; i < probe.warmUp + 1200; i++) {
      el.step(1 / 60);
      if (calm(pose())) { found = i + 1; break; }
    }
    el.remove();
    if (found < 0) throw new Error('no calm, eyes-open, face-forward frame within 20 seconds of idle');
    return { steps: found };
  }
  for (let i = 0; i < steps; i++) el.step(1 / 60);
  const p = pose();
  if (!calm(p)) throw new Error(`frame ${steps} is not calm and eyes-open at ${css}px: ${JSON.stringify(p)}`);
  const copy = document.createElement('canvas');
  copy.width = canvas.width;
  copy.height = canvas.height;
  copy.getContext('2d').drawImage(canvas, 0, 0);
  el.remove();
  window.__last = copy;
  return { pose: p };
}

/* In the page: crop the last render to a `px` image and return it as a PNG data URL. */
function finishInPage({ px, bg, crop, inset }) {
  const src = window.__last;
  const VIEW = { x: 505, y: 405, s: 650 }; /* the rig's icon view */
  const k = src.width / VIEW.s; /* source pixels per rig unit */
  const R = (px / 2) * inset; /* output radius of the crop circle */
  const a = R / crop.r; /* output pixels per rig unit */
  const toOut = (wx, wy) => [px / 2 + (wx - crop.cx) * a, px / 2 + (wy - crop.cy) * a];

  const out = document.createElement('canvas');
  out.width = px;
  out.height = px;
  const ctx = out.getContext('2d');
  if (bg !== 'none') {
    /* the rig's dark background (jig-avatar.js, _drawBackground), in the same rig coordinates */
    const [gx0, gy0] = toOut(500, 470);
    const [gx1, gy1] = toOut(500, 500);
    const g = ctx.createRadialGradient(gx0, gy0, 20 * a, gx1, gy1, 640 * a);
    g.addColorStop(0, 'rgb(22,14,56)');
    g.addColorStop(0.45, 'rgb(10,7,30)');
    g.addColorStop(1, 'rgb(3,2,10)');
    ctx.save();
    if (bg === 'circle') {
      ctx.beginPath();
      ctx.arc(px / 2, px / 2, px / 2, 0, Math.PI * 2);
      ctx.clip();
    }
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, px, px);
    ctx.restore();
  }

  const layer = document.createElement('canvas');
  layer.width = px;
  layer.height = px;
  const l = layer.getContext('2d');
  l.imageSmoothingEnabled = true;
  l.imageSmoothingQuality = 'high';
  /* source pixel (sx, sy) is rig point (VIEW.x + (sx - w/2)/k, VIEW.y + (sy - h/2)/k) */
  const [ox, oy] = toOut(VIEW.x - src.width / 2 / k, VIEW.y - src.height / 2 / k);
  l.drawImage(src, ox, oy, (src.width / k) * a, (src.height / k) * a);
  l.globalCompositeOperation = 'destination-in';
  const fade = l.createRadialGradient(px / 2, px / 2, R * crop.fade, px / 2, px / 2, R);
  fade.addColorStop(0, 'rgba(0,0,0,1)');
  fade.addColorStop(1, 'rgba(0,0,0,0)');
  l.fillStyle = fade;
  l.fillRect(0, 0, px, px);
  ctx.drawImage(layer, 0, 0);
  return out.toDataURL('image/png');
}

/* In the page: one state picture, whole figure in its rounded square. */
async function stateShotInPage({ theme, css, seed, state, task, at }) {
  const el = document.createElement('jig-avatar');
  for (const [k, v] of Object.entries({
    state: 'idle', theme, shape: 'rounded', framing: 'full', clock: 'manual', seed: String(seed),
    style: `width:${css}px;height:${css}px`,
  })) el.setAttribute(k, v);
  document.body.append(el);
  const canvas = el.shadowRoot.querySelector('canvas');
  for (let i = 0; i < 120 && canvas.width !== css * 2; i++) await new Promise(requestAnimationFrame);
  if (canvas.width !== css * 2) throw new Error(`the avatar canvas is ${canvas.width}px, expected ${css * 2}px`);
  el.advance(1);
  el.setState(state, task ? { task } : {});
  el.advance(at);
  for (let i = 0; i < 60 && el._blink !== 0; i++) el.step(1 / 60);
  if (el._blink !== 0) throw new Error(`${state}: the eyes were still mid-blink a second after ${at}s`);
  const url = canvas.toDataURL('image/png');
  el.remove();
  return url;
}

/* In the page: the demo page's 48px icon grid, laid out as in avatar/index.html. Returns nothing; the
 * caller screenshots #strip. */
async function iconStripInPage({ seed, width, height, icons, at }) {
  const strip = document.createElement('div');
  strip.id = 'strip';
  strip.style.cssText = `width:${width}px;height:${height}px;box-sizing:border-box;padding:4px 12px;background:#0e0a20;`
    + 'display:flex;flex-wrap:wrap;gap:18px;align-content:flex-start;font:12px/1.5 "Segoe UI",system-ui,sans-serif;color:#9a90c4';
  const els = icons.map(([label]) => {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;align-items:center;gap:6px;width:72px;text-align:center';
    const el = document.createElement('jig-avatar');
    for (const [k, v] of Object.entries({ shape: 'circle', clock: 'manual', seed: String(seed), style: 'display:block;width:48px;height:48px' })) {
      el.setAttribute(k, v);
    }
    wrap.append(el, Object.assign(document.createElement('span'), { textContent: label }));
    strip.append(wrap);
    return el;
  });
  document.body.append(strip);
  for (let i = 0; i < 120 && els.some((el) => el.shadowRoot.querySelector('canvas').width !== 48); i++) {
    await new Promise(requestAnimationFrame);
  }
  for (const el of els) el.advance(1);
  icons.forEach(([, state, task], i) => els[i].setState(state, task ? { task } : {}));
  for (const el of els) el.advance(at);
  for (let i = 0; i < 60 && els.some((el) => el._blink !== 0); i++) for (const el of els) el.step(1 / 60);
  if (els.some((el) => el._blink !== 0)) throw new Error('the icon strip never had every pair of eyes open at once');
}

function pngFromDataUrl(url) {
  return Buffer.from(url.slice(url.indexOf(',') + 1), 'base64');
}

/* A Windows icon holding PNG images (read by every current browser, and by Windows since Vista). */
function ico(images) {
  const header = Buffer.alloc(6 + 16 * images.length);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  let offset = header.length;
  images.forEach(({ px, png }, i) => {
    const e = 6 + 16 * i;
    header.writeUInt8(px >= 256 ? 0 : px, e);
    header.writeUInt8(px >= 256 ? 0 : px, e + 1);
    header.writeUInt16LE(1, e + 4);
    header.writeUInt16LE(32, e + 6);
    header.writeUInt32LE(png.length, e + 8);
    header.writeUInt32LE(offset, e + 12);
    offset += png.length;
  });
  return Buffer.concat([header, ...images.map((i) => i.png)]);
}

async function write(rel, data) {
  const full = path.join(OUT, rel);
  await mkdir(path.dirname(full), { recursive: true });
  await writeFile(full, data);
  console.log(`wrote ${path.relative(OUT, full)} (${data.length} bytes)`);
}

const server = await serve();
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ deviceScaleFactor: 2, viewport: { width: 1200, height: 1200 } });
  const errors = [];
  page.on('pageerror', (err) => errors.push(err.message));
  page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.evaluate(() => window.jigReady);

  const base = { lookAt: LOOK_AT, seed: SEED };
  const { steps } = await page.evaluate(renderInPage, { ...base, theme: 'dark', css: 128, probe: { warmUp: WARM_UP_STEPS } });
  console.log(`seed ${SEED}: calm, eyes-open, face-forward frame at step ${steps} (${(steps / 60).toFixed(2)} s of idle)`);

  const made = new Map();
  for (const spec of PNGS) {
    await page.evaluate(renderInPage, { ...base, theme: spec.theme, css: spec.srcCss, steps });
    const png = pngFromDataUrl(await page.evaluate(finishInPage, { px: spec.px, bg: spec.bg, crop: spec.crop, inset: spec.inset ?? 1 }));
    made.set(spec.file, { px: spec.px, png });
    await write(spec.file, png);
  }
  await page.evaluate(renderInPage, { seed: SEED, lookAt: LOOK_AT_FULL, theme: README_PICTURE.theme, css: README_PICTURE.px / 2, steps, full: true });
  await write(README_PICTURE.file, pngFromDataUrl(await page.evaluate(() => window.__last.toDataURL('image/png'))));
  await write(ICO.file, ico(ICO.from.map((f) => made.get(f))));
  const { px, png } = made.get(SVG.from);
  await write(SVG.file, Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${px} ${px}" width="${px}" height="${px}">`
    + `<image width="${px}" height="${px}" href="data:image/png;base64,${png.toString('base64')}"/></svg>\n`));
  for (const shot of STATE_SHOTS) {
    const url = await page.evaluate(stateShotInPage, { theme: 'dark', css: README_PICTURE.px / 2, seed: SEED, ...shot });
    await write(shot.file, pngFromDataUrl(url));
  }

  const stripPage = await browser.newPage({ deviceScaleFactor: 1, viewport: { width: 600, height: 600 } });
  stripPage.on('pageerror', (err) => errors.push(err.message));
  await stripPage.goto(`http://127.0.0.1:${server.address().port}/`);
  await stripPage.evaluate(() => window.jigReady);
  await stripPage.evaluate(iconStripInPage, { seed: SEED, ...ICON_STRIP });
  await write(ICON_STRIP.file, await stripPage.locator('#strip').screenshot());
  if (errors.length) throw new Error(`the page reported errors: ${errors.join('; ')}`);
} finally {
  await browser.close();
  server.close();
}
