/* Renders the UI review mockups headlessly.
 * node render.mjs avatar-test        a grid of avatar poses, to pick frames
 * node render.mjs mockups [dir]      every screen of every direction (or one: a, b, c)
 * node render.mjs sheets             one contact sheet per direction
 */
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { mkdirSync, readdirSync } from 'node:fs';
import path from 'node:path';

const require = createRequire('C:/Users/you/Jig/demos/package.json');
const { chromium } = require('playwright');

const JIG = 'C:/Users/you/Jig';
const MOCK = `${JIG}/design/ui-review/mockups`;
const OUT = 'C:/Users/you/.cursor/projects/c-Users-robyn-VideoAvatar/assets/jig-ui-review';
const PORT = 8823;
const ROUTES = [['/web/', `${JIG}/jig/web/`], ['/avatar/', `${JIG}/avatar/`], ['/mock/', `${MOCK}/`], ['/shots/', `${OUT}/`]];
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.png': 'image/png',
  '.svg': 'image/svg+xml', '.ttf': 'font/ttf', '.json': 'application/json', '.ico': 'image/x-icon' };

function serve() {
  const server = createServer(async (req, res) => {
    const url = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const route = ROUTES.find(([p]) => url.startsWith(p));
    if (!route) { res.writeHead(404).end(); return; }
    const file = path.join(route[1], url.slice(route[0].length));
    try {
      await stat(file);
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
      res.end(await readFile(file));
    } catch {
      res.writeHead(404).end();
    }
  });
  return new Promise((resolve) => server.listen(PORT, '127.0.0.1', () => resolve(server)));
}

const SIZES = {
  desktop: { viewport: { width: 1200, height: 800 }, scale: 1 },
  narrow: { viewport: { width: 390, height: 844 }, scale: 2 },
  compact: { viewport: { width: 560, height: 720 }, scale: 1 },
};

/* Which screens each direction renders, and at which sizes/themes. */
const SCREENS = ['home', 'chat', 'approval', 'work', 'work-open', 'work-tests', 'success', 'setup', 'settings', 'off'];
const NARROW = ['home', 'chat', 'approval', 'work', 'work-open', 'work-tests', 'success'];
const COMPACT = ['work'];

async function shot(browser, url, file, size, theme) {
  const s = SIZES[size];
  const context = await browser.newContext({ viewport: s.viewport, deviceScaleFactor: s.scale, colorScheme: theme, locale: 'en-GB' });
  const page = await context.newPage();
  page.on('pageerror', (e) => console.error('pageerror', url, e.message));
  page.on('console', (m) => { if (m.type() === 'error') console.error('console', url, m.text()); });
  await page.goto(url);
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 20000 });
  await page.screenshot({ path: file });
  await context.close();
  console.log('rendered', path.relative(OUT, file));
}

async function mockups(only) {
  const server = await serve();
  const browser = await chromium.launch({ headless: true });
  for (const dir of only ? [only] : ['a', 'b', 'c']) {
    const folder = path.join(OUT, `direction-${dir}`);
    mkdirSync(folder, { recursive: true });
    for (const screen of SCREENS) {
      const jobs = [];
      for (const theme of ['light', 'dark']) {
        jobs.push(['desktop', theme]);
        if (NARROW.includes(screen)) jobs.push(['narrow', theme]);
      }
      if (COMPACT.includes(screen)) jobs.push(['compact', 'light']);
      for (const [size, theme] of jobs) {
        const url = `http://127.0.0.1:${PORT}/mock/mock.html?dir=${dir}&screen=${screen}&theme=${theme}&size=${size}`;
        await shot(browser, url, path.join(folder, `${dir}-${screen}-${size}-${theme}.png`), size, theme);
      }
    }
  }
  await browser.close();
  server.close();
}

async function avatarTest() {
  const server = await serve();
  const browser = await chromium.launch({ headless: true });
  for (const theme of ['light', 'dark']) {
    const url = `http://127.0.0.1:${PORT}/mock/avatar-test.html?theme=${theme}`;
    const context = await browser.newContext({ viewport: { width: 1400, height: 1000 }, colorScheme: theme });
    const page = await context.newPage();
    page.on('pageerror', (e) => console.error('pageerror', e.message));
    await page.goto(url);
    await page.waitForFunction(() => window.__ready === true, null, { timeout: 20000 });
    await page.screenshot({ path: path.join(OUT, `avatar-test-${theme}.png`), fullPage: true });
    await context.close();
  }
  await browser.close();
  server.close();
}

async function sheets() {
  const server = await serve();
  const browser = await chromium.launch({ headless: true });
  for (const dir of ['a', 'b', 'c']) {
    for (const variant of ['overview', 'work']) {
      const url = `http://127.0.0.1:${PORT}/mock/sheet.html?dir=${dir}&variant=${variant}`;
      const context = await browser.newContext({ viewport: { width: 2400, height: 1200 }, deviceScaleFactor: 1 });
      const page = await context.newPage();
      await page.goto(url);
      await page.waitForFunction(() => window.__ready === true, null, { timeout: 30000 });
      await page.screenshot({ path: path.join(OUT, `sheet-${dir}-${variant}.png`), fullPage: true });
      await context.close();
      console.log('sheet', dir, variant);
    }
  }
  for (const variant of ['compare', 'current']) {
    const context = await browser.newContext({ viewport: { width: variant === 'compare' ? 2500 : 2400, height: 1200 } });
    const page = await context.newPage();
    page.on('pageerror', (e) => console.error('pageerror', variant, e.message));
    await page.goto(`http://127.0.0.1:${PORT}/mock/sheet.html?variant=${variant}`);
    await page.waitForFunction(() => window.__ready === true, null, { timeout: 30000 });
    await page.screenshot({ path: path.join(OUT, `sheet-${variant}.png`), fullPage: true });
    await context.close();
    console.log('sheet', variant);
  }
  await browser.close();
  server.close();
}

const mode = process.argv[2];
if (mode === 'avatar-test') await avatarTest();
else if (mode === 'mockups') await mockups(process.argv[3]);
else if (mode === 'sheets') await sheets();
else if (mode === 'serve') { await serve(); console.log(`serving on ${PORT}`); }
else throw new Error('mode: avatar-test | mockups [a|b|c] | sheets | serve');
