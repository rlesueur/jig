// Release cards in the landing-page social card's style (scripts/render_og_image.mjs):
// dark plum, Nunito, the real jig-avatar, and a Beta pill. Same 1200x630 card as the site,
// plus the teaser ratios (4:5, 1:1, 16:9) so the video can end on the same card.
//
//   node assets/jig-release/render-card.mjs
import { createServer } from 'node:http';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const OUT = path.join(ROOT, 'assets', 'jig-release');
const requireFromDemos = createRequire(path.join(ROOT, 'demos', 'package.json'));
const { chromium } = requireFromDemos('playwright');

const SEED = 7;
const IDLE_SECONDS = 4;

const LAYOUTS = [
  { name: 'jig-release-card-1200x630', w: 1200, h: 630, cols: '1fr 420px', pad: '0 48px 0 72px',
    word: 72, beta: 18, title: 48, line: 26, stage: 400, avatar: 370, gap: 20 },
  { name: 'jig-release-card-1080x1350', w: 1080, h: 1350, cols: '1fr', pad: '72px 72px 64px',
    word: 92, beta: 22, title: 52, line: 32, stage: 520, avatar: 480, gap: 28, stack: true },
  { name: 'jig-release-card-1080x1080', w: 1080, h: 1080, cols: '1fr', pad: '56px 64px 48px',
    word: 84, beta: 20, title: 44, line: 28, stage: 420, avatar: 390, gap: 22, stack: true },
  { name: 'jig-release-card-1920x1080', w: 1920, h: 1080, cols: '1fr 640px', pad: '0 96px 0 120px',
    word: 108, beta: 26, title: 64, line: 36, stage: 620, avatar: 580, gap: 28 },
];

function page(L) {
  const flow = L.stack
    ? 'display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center;'
    : 'display: grid; grid-template-columns: ' + L.cols + '; align-items: center;';
  return `<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><style>
@font-face { font-family: "Nunito"; src: url("/fonts/Nunito-Variable.ttf") format("truetype"); font-weight: 200 1000; }
html, body { margin: 0; }
.card {
  width: ${L.w}px; height: ${L.h}px; box-sizing: border-box; overflow: hidden; position: relative;
  ${flow} gap: ${L.gap}px; padding: ${L.pad};
  font-family: "Nunito", sans-serif; color: #fdf0e6;
  background:
    radial-gradient(760px 520px at 8% -14%, #3b1f35 0%, transparent 62%),
    radial-gradient(700px 520px at 100% 6%, #2d1834 0%, transparent 60%),
    #1b1220;
}
.lockup { display: flex; align-items: center; gap: 18px; ${L.stack ? 'justify-content: center;' : ''} margin: 0 0 18px; }
.wordmark {
  margin: 0; font-size: ${L.word}px; font-weight: 900; line-height: 1;
  background: linear-gradient(90deg, #ffb661, #ff9f80 55%, #f5aed4); -webkit-background-clip: text; background-clip: text; color: transparent;
}
.beta {
  display: inline-flex; align-items: center; height: ${Math.round(L.beta * 1.7)}px; padding: 0 ${Math.round(L.beta * 0.7)}px;
  border-radius: 999px; font-size: ${L.beta}px; font-weight: 800; color: #dcc5cc;
  background: #2b1c2e; border: 2px solid #4b3450;
}
h1 { margin: 0 0 22px; font-size: ${L.title}px; line-height: 1.08; font-weight: 900; letter-spacing: -0.01em; }
.where { margin: 0; font-size: ${L.line}px; font-weight: 800; color: #fdf0e6; }
.cmd { margin: 10px 0 0; font-size: ${Math.round(L.line * 0.92)}px; font-weight: 700; color: #dcc5cc; }
.copy { ${L.stack ? '' : ''} }
.stage { width: ${L.stage}px; height: ${L.stage}px; border-radius: 40px; background: #2b1c2e; border: 2px solid #4b3450; display: grid; place-items: center; }
jig-avatar { display: block; width: ${L.avatar}px; height: ${L.avatar}px; }
</style></head><body>
<div class="card" id="card">
  <div class="copy">
    <div class="lockup"><p class="wordmark">Jig</p><span class="beta">Beta</span></div>
    <h1>Version 0.1.0b1</h1>
    <p class="where">github.com/rlesueur/Jig</p>
    <p class="cmd">git clone https://github.com/rlesueur/jig.git</p>
  </div>
  <div class="stage">
    <jig-avatar id="avatar" state="idle" theme="dark" framing="full" shape="none" clock="manual" seed="${SEED}"></jig-avatar>
  </div>
</div>
<script type="module">
import '/avatar/jig-avatar.js';
window.jigReady = Promise.all([customElements.whenDefined('jig-avatar'), document.fonts.load('900 ${L.word}px Nunito')]).then(() => true);
</script></body></html>`;
}

const FILES = {
  '/avatar/jig-avatar.js': ['avatar/jig-avatar.js', 'text/javascript; charset=utf-8'],
  '/fonts/Nunito-Variable.ttf': ['jig/web/fonts/Nunito-Variable.ttf', 'font/ttf'],
};

function serve(html) {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/') {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      res.end(html);
      return;
    }
    const file = FILES[url.pathname];
    if (!file) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'content-type': file[1] });
    res.end(await readFile(path.join(ROOT, file[0])));
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
try {
  for (const L of LAYOUTS) {
    const server = await serve(page(L));
    try {
      const pageObj = await browser.newPage({ deviceScaleFactor: 1, viewport: { width: L.w, height: L.h } });
      const errors = [];
      pageObj.on('pageerror', (err) => errors.push(err.message));
      pageObj.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
      await pageObj.goto(`http://127.0.0.1:${server.address().port}/`);
      await pageObj.evaluate(() => window.jigReady);
      const used = await pageObj.evaluate((px) => document.fonts.check(`900 ${px}px Nunito`), L.word);
      if (!used) throw new Error('Nunito did not load');
      await pageObj.evaluate(async (seconds) => {
        const el = document.getElementById('avatar');
        const canvas = el.shadowRoot.querySelector('canvas');
        for (let i = 0; i < 120 && canvas.width === 0; i++) await new Promise(requestAnimationFrame);
        el.advance(seconds);
        for (let i = 0; i < 120 && el._blink !== 0; i++) el.step(1 / 60);
      }, IDLE_SECONDS);
      const png = await pageObj.locator('#card').screenshot({ type: 'png' });
      if (errors.length) throw new Error(errors.join('; '));
      const dest = path.join(OUT, `${L.name}.png`);
      await writeFile(dest, png);
      console.log(`wrote ${dest} (${png.length} bytes)`);
      await pageObj.close();
    } finally {
      server.close();
    }
  }
} finally {
  await browser.close();
}
