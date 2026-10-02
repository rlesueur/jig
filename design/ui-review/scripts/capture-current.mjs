/* Screenshots of the current Jig UI from a spare Jig, light and dark, desktop and narrow. Headless only.
 * node capture-current.mjs <phase>   phases: main | settings | setup | off
 */
import { createRequire } from 'node:module';
import { readFileSync, mkdirSync, appendFileSync } from 'node:fs';
import path from 'node:path';

const require = createRequire('C:/Users/you/Jig/demos/package.json');
const { chromium } = require('playwright');

const OUT = 'C:/Users/you/.cursor/projects/c-Users-robyn-VideoAvatar/assets/jig-ui-review/current';
const TMP = path.join(process.env.TEMP, 'jig-ui-review');
mkdirSync(OUT, { recursive: true });
const LOG = path.join(OUT, 'capture-log.txt');
const log = (...a) => { const s = `[${new Date().toISOString().slice(11, 19)}] ${a.join(' ')}`; console.log(s); appendFileSync(LOG, `${s}\n`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const DESKTOP = { width: 1280, height: 820 };
const NARROW = { width: 390, height: 844 };

async function open(port, which) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: DESKTOP, deviceScaleFactor: 1, colorScheme: 'light', locale: 'en-GB', timezoneId: 'Europe/London' });
  const base = `http://127.0.0.1:${port}`;
  const token = readFileSync(path.join(TMP, which, 'token.txt'), 'utf8').trim().split(/\r?\n/).pop().trim();
  const r = await context.request.post(`${base}/auth/session`, { data: { token } });
  if (!r.ok()) throw new Error(`sign-in failed ${r.status()}`);
  const page = await context.newPage();
  page.on('pageerror', (e) => log('pageerror', e.message));
  await page.goto(base);
  await page.waitForTimeout(2500);
  return { browser, context, page, base, token };
}

/** Hide the model's name in shots (no model names in review material). */
async function scrub(page) {
  await page.evaluate(() => {
    for (const id of ['st-model', 'st-agent-where', 'st-sentinel-where']) {
      const n = document.getElementById(id);
      if (n && /\S/.test(n.textContent) && n.textContent !== '—') n.textContent = n.textContent.replace(/[\w.-]*\d+b[\w.-]*/gi, 'local model');
    }
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let t = walker.nextNode(); t; t = walker.nextNode()) {
      if (/bonsai/i.test(t.nodeValue)) t.nodeValue = t.nodeValue.replace(/bonsai[\w.-]*/gi, 'local model');
    }
  });
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
      await page.screenshot({ path: file, fullPage: full });
      log('shot', path.basename(file));
    }
  }
  await setTheme(page, 'light');
  await page.setViewportSize(DESKTOP);
}

const jigState = (page) => page.evaluate(() => document.documentElement.dataset.jigState || '');

async function send(page, text) {
  await page.fill('#chat-input', text);
  await page.click('#chat-send');
}

async function waitIdleChat(page, timeoutMs) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const busy = await page.evaluate(() => document.getElementById('chat-send').disabled);
    if (!busy) return true;
    await sleep(1000);
  }
  return false;
}

async function main() {
  const { browser, page, context, base } = await open(8820, 'a');
  await shoot(page, '01-chat-idle');

  log('sending a quick question');
  await send(page, 'Give me three friendly name ideas for a ginger cat.');
  for (let i = 0; i < 60; i++) {
    const s = await jigState(page);
    if (s === 'thinking' || s === 'talking') break;
    await sleep(250);
  }
  log('state', await jigState(page));
  await shoot(page, '02-chat-thinking', { sizes: ['desktop'], themes: ['light'] });
  await shoot(page, '02-chat-thinking-narrow', { sizes: ['narrow'], themes: ['dark'] });
  await waitIdleChat(page, 240000);
  await sleep(1500);
  await shoot(page, '03-chat-reply');

  log('coding task');
  await send(page, 'In your workspace, write a small Python module palindrome.py with a function is_palindrome(text) that ignores case, spaces and punctuation, then write tests for it in test_palindrome.py and run them. If any fail, fix the code and run them again.');
  const end = Date.now() + 12 * 60000;
  let approvals = 0;
  const seenTools = new Set();
  while (Date.now() < end) {
    const pending = await page.$('.ask.is-pending .btn-yes');
    if (pending) {
      approvals += 1;
      log('approval', approvals);
      await page.locator('.ask.is-pending').last().scrollIntoViewIfNeeded();
      if (approvals === 1) {
        await shoot(page, '04-approval-card');
        const why = await page.$('.ask.is-pending .why-ask > summary');
        if (why) {
          await why.click();
          await sleep(500);
          await shoot(page, '05-approval-card-why', { sizes: ['desktop'], themes: ['light', 'dark'] });
          await why.click();
        }
      }
      await page.locator('.ask.is-pending .btn-yes').last().click();
      await sleep(1500);
      continue;
    }
    const s = await jigState(page);
    const doing = await page.evaluate(() => document.getElementById('avatar-says').textContent);
    const key = `${s}:${doing}`;
    if (s === 'working' && !seenTools.has(key) && seenTools.size < 3) {
      seenTools.add(key);
      await shoot(page, `06-coding-working-${seenTools.size}`, { sizes: seenTools.size === 1 ? ['desktop', 'narrow'] : ['desktop'], themes: seenTools.size === 1 ? ['light', 'dark'] : ['light'] });
    }
    const busy = await page.evaluate(() => document.getElementById('chat-send').disabled);
    if (!busy) break;
    await sleep(700);
  }
  await sleep(1500);
  await shoot(page, '07-coding-done');
  await page.evaluate(() => { const c = document.getElementById('show-working'); if (c && !c.checked) c.click(); });
  await sleep(500);
  await shoot(page, '08-coding-done-show-working', { sizes: ['desktop', 'narrow'], themes: ['light', 'dark'], full: false });
  await page.evaluate(() => { const c = document.getElementById('show-working'); if (c && c.checked) c.click(); });

  log('background job');
  const token = readFileSync(path.join(TMP, 'a', 'token.txt'), 'utf8').trim().split(/\r?\n/).pop().trim();
  await context.request.post(`${base}/tasks`, { headers: { Authorization: `Bearer ${token}` },
    data: { title: 'Morning headlines', description: 'Read https://www.bbc.co.uk/news and list the five top headlines with one line on each.', mode: 'research' } });
  for (let i = 0; i < 80; i++) {
    const busy = await page.evaluate(() => document.getElementById('doing').dataset.busy);
    if (busy === 'true') break;
    await sleep(250);
  }
  await sleep(2500);
  await shoot(page, '09-background-job');
  await page.click('#doing-open');
  await sleep(800);
  await shoot(page, '10-activity');
  await page.evaluate(() => document.querySelector('#activity-more').open = true);
  await sleep(500);
  await shoot(page, '11-activity-everything', { sizes: ['desktop'], themes: ['light', 'dark'] });
  await page.click('#activity-close');
  await browser.close();
}

async function settings() {
  const { browser, page } = await open(8820, 'a');
  const sections = ['model', 'rules', 'connections', 'memory', 'conversations', 'schedules', 'history', 'startup', 'devices', 'power', 'appearance', 'chat'];
  for (const [i, s] of sections.entries()) {
    await page.goto(`${page.url().split('#')[0]}#settings/${s}`);
    await sleep(1500);
    const big = ['model', 'rules', 'connections'].includes(s);
    await shoot(page, `20-settings-${String(i + 1).padStart(2, '0')}-${s}`, { sizes: big ? ['desktop', 'narrow'] : ['desktop'], themes: ['light', 'dark'] });
  }
  await browser.close();
}

async function setup() {
  const { browser, page } = await open(8821, 'b');
  await sleep(1500);
  await shoot(page, '30-setup-1');
  const buttons = await page.$$eval('#setup button, #setup a.btn', (bs) => bs.map((b) => b.textContent.trim()));
  log('setup buttons', JSON.stringify(buttons));
  await browser.close();
}

async function off() {
  const { browser, page } = await open(8820, 'a');
  await page.goto(`${page.url().split('#')[0]}#settings/power`);
  await sleep(2000);
  await page.click('#power-off');
  await sleep(800);
  await shoot(page, '40-power-confirm', { sizes: ['desktop'], themes: ['light'] });
  await page.click('#confirm-ok');
  await sleep(1500);
  await shoot(page, '41-turning-off', { sizes: ['desktop'], themes: ['light', 'dark'] });
  await sleep(12000);
  await shoot(page, '42-jig-is-off');
  await browser.close();
}

async function coding2() {
  const { browser, page } = await open(8820, 'a');
  const metrics = () => page.evaluate(() => ({
    doc: [document.scrollingElement.scrollHeight, innerHeight, document.scrollingElement.scrollTop],
    log: (() => { const l = document.getElementById('chat-log'); return [l.scrollHeight, l.clientHeight]; })(),
  }));
  await page.click('#chat-new').catch(() => {});
  await sleep(800);
  await send(page, 'In your workspace, write word_count.py with a function count_words(text) that counts words, ignoring punctuation, plus tests in test_word_count.py using unittest, and run the tests. Fix anything that fails.');
  const end = Date.now() + 10 * 60000;
  let shots = 0;
  let lastKey = '';
  while (Date.now() < end) {
    const yes = await page.$('.ask.is-pending .btn-yes');
    if (yes) {
      await yes.click();
      await sleep(300);
      continue;
    }
    const s = await jigState(page);
    const says = await page.evaluate(() => document.getElementById('avatar-says').textContent);
    const key = `${s}|${says}`;
    if ((s === 'working' || s === 'thinking' || s === 'talking') && key !== lastKey && shots < 6) {
      lastKey = key;
      shots += 1;
      log('state', key, JSON.stringify(await metrics()));
      await page.screenshot({ path: path.join(OUT, `06-coding-live-${shots}-${s}-desktop-light.png`) });
    }
    if (!(await page.evaluate(() => document.getElementById('chat-send').disabled))) break;
    await sleep(120);
  }
  log('end metrics', JSON.stringify(await metrics()));
  await page.setViewportSize(NARROW);
  await sleep(600);
  log('narrow metrics', JSON.stringify(await metrics()));
  await page.screenshot({ path: path.join(OUT, '07b-coding-done-narrow-light-noscroll.png') });
  await browser.close();
}

const phase = process.argv[2] || 'main';
const fns = { main, settings, setup, off, coding2 };
await fns[phase]();
log('done', phase);
