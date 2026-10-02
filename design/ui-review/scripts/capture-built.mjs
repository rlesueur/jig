/* Screenshots of the built Direction A UI from a spare Jig, light and dark, desktop and narrow. Headless only.
 * node capture-built.mjs <phase> [port] [which]
 *   phases: setup | home | chat | coding | background | settings | walkthrough | off | check
 * Each shot also checks the page: it must not scroll as a whole, there must be exactly one Jig visible,
 * and no Jig may be drawn smaller than 112px.
 */
import { createRequire } from 'node:module';
import { readFileSync, mkdirSync, appendFileSync } from 'node:fs';
import path from 'node:path';

const require = createRequire('C:/Users/you/Jig/demos/package.json');
const { chromium } = require('playwright');

const OUT = 'C:/Users/you/.cursor/projects/c-Users-robyn-VideoAvatar/assets/jig-ui-built';
const TMP = path.join(process.env.TEMP, 'jig-ui-review');
mkdirSync(OUT, { recursive: true });
const LOG = path.join(OUT, 'capture-log.txt');
const log = (...a) => { const s = `[${new Date().toISOString().slice(11, 19)}] ${a.join(' ')}`; console.log(s); appendFileSync(LOG, `${s}\n`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const DESKTOP = { width: 1280, height: 820 };
const NARROW = { width: 390, height: 844 };
const problems = [];

async function open(port, which) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: DESKTOP, deviceScaleFactor: 1, colorScheme: 'light', locale: 'en-GB', timezoneId: 'Europe/London' });
  const base = `http://127.0.0.1:${port}`;
  const token = readFileSync(path.join(TMP, which, 'token.txt'), 'utf8').trim().split(/\r?\n/).pop().trim();
  const r = await context.request.post(`${base}/auth/session`, { data: { token } });
  if (!r.ok()) throw new Error(`sign-in failed ${r.status()}`);
  const page = await context.newPage();
  page.on('pageerror', (e) => { log('PAGEERROR', e.message); problems.push(`pageerror: ${e.message}`); });
  page.on('console', (m) => { if (m.type() === 'error') log('console error', m.text()); });
  await page.goto(base);
  await page.waitForTimeout(2500);
  return { browser, context, page, base, token };
}

/** No model names in the shots. */
async function scrub(page) {
  await page.evaluate(() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let t = walker.nextNode(); t; t = walker.nextNode()) {
      if (/bonsai|qwen|gemma|llama|mistral|gpt-oss|granite|phi\d/i.test(t.nodeValue)) t.nodeValue = t.nodeValue.replace(/[\w.-]*(bonsai|qwen|gemma|llama|mistral|gpt-oss|granite|phi\d)[\w.:-]*/gi, 'local model');
    }
  });
}

async function check(page, name) {
  const r = await page.evaluate(() => {
    const se = document.scrollingElement;
    const visible = (n) => {
      const b = n.getBoundingClientRect();
      const s = getComputedStyle(n);
      return b.width > 0 && b.height > 0 && s.visibility !== 'hidden' && n.offsetParent !== null;
    };
    const jigs = [...document.querySelectorAll('jig-avatar')].filter(visible).map((n) => Math.round(n.getBoundingClientRect().width));
    const appShown = !document.getElementById('app').hidden;
    const describe = (n) => `${n.tagName.toLowerCase()}${n.id ? `#${n.id}` : ''}${n.classList.length ? `.${[...n.classList].join('.')}` : ''}`;
    const below = [];
    const walk = (n, depth) => {
      for (const c of n.children) {
        const b = c.getBoundingClientRect();
        if (b.bottom > innerHeight + 1 && getComputedStyle(c).position !== 'fixed') {
          below.push(`${describe(c)}@${Math.round(b.bottom)}`);
          if (depth < 4) walk(c, depth + 1);
        }
      }
    };
    walk(document.body, 0);
    return { pageScroll: se.scrollHeight - innerHeight, scrolled: [se.scrollTop, document.body.scrollTop], appShown, jigs, below: below.slice(0, 8) };
  });
  const bad = [];
  if (r.appShown && r.pageScroll > 1) bad.push(`page scrolls by ${r.pageScroll}px (at ${r.scrolled}; below: ${r.below.join(', ')})`);
  if (r.jigs.length !== 1) bad.push(`${r.jigs.length} Jigs visible`);
  if (r.jigs.some((w) => w < 112)) bad.push(`a Jig is ${Math.min(...r.jigs)}px`);
  if (bad.length) {
    problems.push(`${name}: ${bad.join('; ')}`);
    log('PROBLEM', name, bad.join('; '));
  }
}

/** WCAG AA for every visible piece of text on the page, against its effective background (both ends of a
 * linear gradient; the page's soft radial glow is ignored). */
async function contrast(page, name) {
  const fails = await page.evaluate(() => {
    const parse = (c) => {
      const m = c.match(/[\d.]+/g).map(Number);
      if (c.startsWith('color(srgb')) return { r: m[0] * 255, g: m[1] * 255, b: m[2] * 255, a: m[3] ?? 1 };
      return { r: m[0], g: m[1], b: m[2], a: m[3] ?? 1 };
    };
    const lum = ({ r, g, b }) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
    const blend = (top, bottom) => ({ r: top.r * top.a + bottom.r * (1 - top.a), g: top.g * top.a + bottom.g * (1 - top.a), b: top.b * top.a + bottom.b * (1 - top.a), a: 1 });
    const ratio = (a, b) => { const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x); return (l1 + 0.05) / (l2 + 0.05); };
    function backgrounds(el) {
      // every possible background under el: one per gradient end it sits on
      let stacks = [[]];
      for (let n = el; n && n !== document.body; n = n.parentElement) {
        const cs = getComputedStyle(n);
        const c = parse(cs.backgroundColor);
        const img = cs.backgroundImage;
        if (img !== 'none' && img.includes('linear-gradient')) {
          const stops = (img.match(/(rgba?\([^)]*\)|color\(srgb[^)]*\))/g) || []).map(parse);
          const ends = [stops[0], stops.at(-1)].filter(Boolean);
          stacks = stacks.flatMap((s) => ends.map((e) => [...s, ...(c.a > 0 ? [c] : []), e]));
          if (ends.every((e) => e.a >= 1)) break;
          continue;
        }
        if (c.a > 0) stacks = stacks.map((s) => [...s, c]);
        if (c.a >= 1) break;
      }
      let base = parse(getComputedStyle(document.body).backgroundColor);
      if (base.a < 1) base = { r: 255, g: 255, b: 255, a: 1 };
      return stacks.map((s) => s.reverse().reduce((out, l) => blend(l, out), base));
    }
    const out = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const seen = new Set();
    for (let t = walker.nextNode(); t; t = walker.nextNode()) {
      const el = t.parentElement;
      if (!t.nodeValue.trim() || seen.has(el) || !el.offsetParent || el.closest('.sr-only, [hidden], jig-avatar, script, style')) continue;
      seen.add(el);
      const box = el.getBoundingClientRect();
      if (box.width < 1 || box.height < 1 || box.bottom < 0 || box.top > innerHeight) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.backgroundClip === 'text' || cs.webkitBackgroundClip === 'text') continue;
      const opacity = [...function* () { for (let n = el; n; n = n.parentElement) yield Number(getComputedStyle(n).opacity); }()].reduce((a, b) => a * b, 1);
      if (opacity < 0.95) continue; // a dimmed page behind a sheet
      const fg = parse(cs.color);
      const size = parseFloat(cs.fontSize);
      const large = size >= 24 || (size >= 18.66 && Number(cs.fontWeight) >= 700);
      const need = large ? 3 : 4.5;
      const worst = Math.min(...backgrounds(el).map((bg) => ratio(blend(fg, bg), bg)));
      const disabled = el.closest(':disabled, [aria-disabled="true"]');
      if (worst < need && !disabled) {
        const d = `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}${el.classList.length ? `.${[...el.classList].join('.')}` : ''}`;
        out.push(`${d} "${t.nodeValue.trim().slice(0, 30)}" ${worst.toFixed(2)} < ${need}`);
      }
    }
    return out;
  });
  if (fails.length) {
    problems.push(`${name}: contrast ${fails.join('; ')}`);
    log('CONTRAST', name, fails.join(' | '));
  }
}

async function setTheme(page, theme) {
  await page.evaluate((t) => window.jigAppearance.set(t), theme);
}

async function shoot(page, name, { sizes = ['desktop', 'narrow'], themes = ['light', 'dark'], full = false } = {}) {
  for (const theme of themes) {
    await setTheme(page, theme);
    for (const size of sizes) {
      await page.setViewportSize(size === 'desktop' ? DESKTOP : NARROW);
      await page.waitForTimeout(700);
      await scrub(page);
      const file = path.join(OUT, `${name}-${size}-${theme}.png`);
      await check(page, `${name}-${size}-${theme}`);
      await contrast(page, `${name}-${size}-${theme}`);
      await page.screenshot({ path: file, fullPage: full });
      log('shot', path.basename(file));
    }
  }
  await setTheme(page, 'light');
  await page.setViewportSize(DESKTOP);
}

const jigState = (page) => page.evaluate(() => document.documentElement.dataset.jigState || '');
const busy = (page) => page.evaluate(() => document.getElementById('chat-send').disabled);

async function send(page, text) {
  await page.fill('#chat-input', text);
  await page.click('#chat-send');
}

async function waitIdleChat(page, timeoutMs) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (!(await busy(page))) return true;
    await sleep(1000);
  }
  return false;
}

async function setup(port = 8821, which = 'b') {
  const { browser, page } = await open(port, which);
  await sleep(1500);
  await shoot(page, '30-setup');
  await browser.close();
}

async function home(port = 8820, which = 'a') {
  const { browser, page } = await open(port, which);
  await shoot(page, '01-home');
  await browser.close();
}

async function chat(port = 8820, which = 'a') {
  const { browser, page } = await open(port, which);
  await send(page, 'Give me three friendly name ideas for a ginger cat.');
  for (let i = 0; i < 80; i++) {
    if (['thinking', 'talking'].includes(await jigState(page))) break;
    await sleep(250);
  }
  await shoot(page, '02-chat-thinking', { sizes: ['desktop'], themes: ['light'] });
  await waitIdleChat(page, 240000);
  for (let i = 0; i < 20 && (await jigState(page)) !== 'success'; i++) await sleep(150);
  await shoot(page, '03-chat-success', { sizes: ['desktop', 'narrow'], themes: ['light'] });
  await sleep(3500);
  await shoot(page, '04-chat-reply');
  await browser.close();
}

const CODING = 'In your workspace, make a folder budget with budget.py: a function parse_amount(text) that turns text like "£1,250.00" or "12.5" into a float, and total(rows) that adds up the amounts in a list of strings. Write unittest tests in budget/test_budget.py, including amounts with a £ sign and commas, and run them with python3 -m unittest -v from the budget folder. If any fail, fix the code and run the tests again until they pass.';

async function coding(port = 8820, which = 'a') {
  const { browser, page } = await open(port, which);
  const end = Date.now() + 15 * 60000;
  await send(page, CODING);
  let approvals = 0;
  const posed = new Set();
  let stepsShot = false;
  while (Date.now() < end) {
    const yes = page.locator('#corner-held .ask.is-pending .btn-yes').first();
    if (await yes.count()) {
      approvals += 1;
      if (approvals === 1) {
        await shoot(page, '10-coding-approval');
        await page.locator('#corner-held .ask.is-pending .why-ask > summary').first().click();
        await sleep(400);
        await shoot(page, '11-coding-approval-why', { sizes: ['desktop'], themes: ['light', 'dark'] });
        await page.locator('#corner-held .ask.is-pending .why-ask > summary').first().click();
      }
      await yes.click();
      await sleep(300);
      continue;
    }
    const s = await jigState(page);
    const variant = await page.evaluate(() => document.getElementById('avatar').task || '');
    if (s === 'working' && !posed.has(variant) && posed.size < 3) {
      posed.add(variant);
      log('pose', s, variant);
      await shoot(page, `12-coding-working-${variant}`, { sizes: ['desktop'], themes: ['light'] });
    }
    const tested = await page.locator('[data-testid="work-meter"]').count();
    if (tested && !stepsShot) {
      stepsShot = true;
      await shoot(page, '13-coding-tests-ran', { sizes: ['desktop', 'narrow'], themes: ['light', 'dark'] });
    }
    if (!(await busy(page))) break;
    await sleep(150);
  }
  log('approvals', approvals, 'poses', [...posed].join(','));
  await sleep(1200);
  await shoot(page, '14-coding-done');
  await page.click('[data-testid="work-steps-toggle"]');
  await sleep(600);
  await shoot(page, '15-coding-steps', { sizes: ['desktop'], themes: ['light', 'dark'] });
  // open the first test run and the last change
  const tests = page.locator('[data-testid="step"]').filter({ hasText: 'Ran the tests' });
  if (await tests.count()) await tests.first().locator('[data-testid="step-toggle"]').click();
  await sleep(400);
  await shoot(page, '16-coding-steps-tests', { sizes: ['desktop'], themes: ['light', 'dark'] });
  if (await tests.count()) await tests.first().locator('[data-testid="step-toggle"]').click();
  const changed = page.locator('[data-testid="step"]').filter({ hasText: /^Changed / });
  const writes = page.locator('[data-testid="step"][data-tool="write_file"]');
  const anyWrite = (await changed.count()) ? changed.last() : writes.last();
  if (await anyWrite.count()) {
    await anyWrite.locator('[data-testid="step-toggle"]').click();
    await sleep(400);
    await anyWrite.scrollIntoViewIfNeeded();
    await shoot(page, '17-coding-steps-diff', { sizes: ['desktop'], themes: ['light', 'dark'] });
  } else log('no file was written, so no diff shot');
  // narrow: the steps in the sheet
  await page.setViewportSize(NARROW);
  await sleep(600);
  await shoot(page, '18-coding-steps-sheet', { sizes: ['narrow'], themes: ['light', 'dark'] });
  await browser.close();
}

async function background(port = 8820, which = 'a') {
  const { browser, page, context, base, token } = await open(port, which);
  await context.request.post(`${base}/tasks`, { headers: { Authorization: `Bearer ${token}` },
    data: { title: 'Morning headlines', description: 'Read https://www.bbc.co.uk/news and list the five top headlines with one line on each.', mode: 'research' } });
  for (let i = 0; i < 80; i++) {
    if ((await page.evaluate(() => document.getElementById('doing').dataset.busy)) === 'true') break;
    await sleep(250);
  }
  await sleep(2500);
  await shoot(page, '20-background-job');
  await page.click('#doing-open');
  await sleep(800);
  await shoot(page, '21-activity', { sizes: ['desktop', 'narrow'], themes: ['light'] });
  await page.click('#activity-close');
  await browser.close();
}

async function settings(port = 8820, which = 'a') {
  const { browser, page } = await open(port, which);
  const sections = ['model', 'rules', 'connections', 'memory', 'conversations', 'schedules', 'history', 'startup', 'devices', 'power', 'appearance', 'chat'];
  for (const [i, s] of sections.entries()) {
    await page.goto(`${page.url().split('#')[0]}#settings/${s}`);
    await sleep(1500);
    const big = ['model', 'rules', 'connections', 'memory'].includes(s);
    await shoot(page, `40-settings-${String(i + 1).padStart(2, '0')}-${s}`, { sizes: big ? ['desktop', 'narrow'] : ['desktop'], themes: big ? ['light', 'dark'] : ['light'] });
  }
  await browser.close();
}

async function walkthrough(port = 8820, which = 'a') {
  const { browser, page } = await open(port, which);
  await page.goto(`${page.url().split('#')[0]}#settings/connections/github`);
  await sleep(2000);
  await shoot(page, '45-walkthrough');
  const next = page.getByTestId('walkthrough-next');
  if (await next.isEnabled()) {
    await next.click();
    await sleep(800);
    await shoot(page, '46-walkthrough-step-2', { sizes: ['desktop'], themes: ['light', 'dark'] });
  }
  await browser.close();
}

async function off(port = 8820, which = 'a') {
  const { browser, page } = await open(port, which);
  await page.goto(`${page.url().split('#')[0]}#settings/power`);
  await sleep(2000);
  await page.click('#power-off');
  await sleep(800);
  await page.click('#confirm-ok');
  await sleep(1500);
  await shoot(page, '50-turning-off', { sizes: ['desktop'], themes: ['light'] });
  await sleep(12000);
  await shoot(page, '51-jig-is-off');
  await browser.close();
}

const phase = process.argv[2] || 'home';
const port = process.argv[3] ? Number(process.argv[3]) : undefined;
const which = process.argv[4];
const fns = { setup, home, chat, coding, background, settings, walkthrough, off };
await fns[phase](port, which);
log('done', phase, problems.length ? `PROBLEMS: ${problems.join(' | ')}` : 'no problems');
if (problems.length) process.exitCode = 1;
