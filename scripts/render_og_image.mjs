// Renders the landing page's social card, site/media/og-image-v2.png (1200 x 627, LinkedIn's
// recommended size), in the site's dark theme: the wordmark, the hero headline and the real
// <jig-avatar> rig from avatar/jig-avatar.js, in headless Chromium on the manual clock with a fixed
// seed, so every run gives the same picture. PNG keeps the type sharp; the earlier JPEG was small
// enough that LinkedIn's preview looked pixelated.
//
//   node scripts/render_og_image.mjs [--out FILE]
//
// Playwright comes from demos/ (run "npm ci" there once). The font is the web UI's Nunito.
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
const outIdx = args.indexOf('--out');
if (outIdx >= 0 && !args[outIdx + 1]) throw new Error('--out needs a file');
const OUT = outIdx >= 0 ? path.resolve(args[outIdx + 1]) : path.join(ROOT, 'site', 'media', 'og-image-v2.png');

const SEED = 7;
const IDLE_SECONDS = 4;
const WIDTH = 1200;
const HEIGHT = 627;

const PAGE = `<!doctype html><html lang="en-GB"><head><meta charset="utf-8">
<style>
@font-face { font-family: "Nunito"; src: url("/fonts/Nunito-Variable.ttf") format("truetype"); font-weight: 200 1000; }
html, body { margin: 0; }
.card {
  width: ${WIDTH}px; height: ${HEIGHT}px; box-sizing: border-box; overflow: hidden; position: relative;
  display: grid; grid-template-columns: 1fr 470px; align-items: center; gap: 24px; padding: 0 56px 0 84px;
  font-family: "Nunito", sans-serif; color: #fdf0e6;
  background:
    radial-gradient(760px 520px at 8% -14%, #3b1f35 0%, transparent 62%),
    radial-gradient(700px 520px at 100% 6%, #2d1834 0%, transparent 60%),
    #1b1220;
}
.wordmark {
  margin: 0 0 26px; font-size: 64px; font-weight: 900; line-height: 1;
  background: linear-gradient(90deg, #ffb661, #ff9f80 55%, #f5aed4); -webkit-background-clip: text; background-clip: text; color: transparent;
}
h1 { margin: 0 0 26px; font-size: 62px; line-height: 1.08; font-weight: 900; letter-spacing: -0.01em; }
.grad { background: linear-gradient(90deg, #ffb661, #ff9f80 55%, #f5aed4); -webkit-background-clip: text; background-clip: text; color: transparent; }
.sub { margin: 0; font-size: 28px; font-weight: 700; color: #dcc5cc; }
.stage { width: 470px; height: 470px; border-radius: 40px; background: #2b1c2e; border: 2px solid #4b3450; display: grid; place-items: center; }
jig-avatar { display: block; width: 440px; height: 440px; }
</style></head><body>
<div class="card" id="card">
  <div>
    <p class="wordmark">Jig</p>
    <h1>An always-on AI agent.<br><span class="grad">On your own machine.</span></h1>
    <p class="sub">Open source · Apache-2.0</p>
  </div>
  <div class="stage">
    <jig-avatar id="avatar" state="idle" theme="dark" framing="full" shape="none" clock="manual" seed="${SEED}"></jig-avatar>
  </div>
</div>
<script type="module">
import '/avatar/jig-avatar.js';
window.jigReady = Promise.all([customElements.whenDefined('jig-avatar'), document.fonts.load('900 62px Nunito')]).then(() => true);
</script></body></html>`;

const FILES = {
  '/avatar/jig-avatar.js': ['avatar/jig-avatar.js', 'text/javascript; charset=utf-8'],
  '/fonts/Nunito-Variable.ttf': ['jig/web/fonts/Nunito-Variable.ttf', 'font/ttf'],
};

function serve() {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/') {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      res.end(PAGE);
      return;
    }
    const file = FILES[url.pathname];
    if (!file) {
      res.writeHead(404);
      res.end();
      return;
    }
    res.writeHead(200, { 'content-type': file[1] });
    res.end(await readFile(path.join(ROOT, file[0])));
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

const server = await serve();
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ deviceScaleFactor: 1, viewport: { width: WIDTH, height: HEIGHT } });
  const errors = [];
  page.on('pageerror', (err) => errors.push(err.message));
  page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.evaluate(() => window.jigReady);
  const used = await page.evaluate(() => document.fonts.check('900 62px Nunito'));
  if (!used) throw new Error('Nunito did not load, so the card would be set in a fallback font');
  await page.evaluate(async (seconds) => {
    const el = document.getElementById('avatar');
    const canvas = el.shadowRoot.querySelector('canvas');
    for (let i = 0; i < 120 && canvas.width === 0; i++) await new Promise(requestAnimationFrame);
    el.advance(seconds);
    for (let i = 0; i < 120 && el._blink !== 0; i++) el.step(1 / 60);
    if (el._blink !== 0) throw new Error('the eyes were still mid-blink two seconds after the chosen moment');
  }, IDLE_SECONDS);
  const png = await page.locator('#card').screenshot({ type: 'png' });
  if (errors.length) throw new Error(`the page reported errors: ${errors.join('; ')}`);
  await mkdir(path.dirname(OUT), { recursive: true });
  await writeFile(OUT, png);
  console.log(`wrote ${OUT} (${png.length} bytes)`);
} finally {
  await browser.close();
  server.close();
}
