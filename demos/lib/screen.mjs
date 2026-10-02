/*
 * Records the real Jig web UI in headless Chromium. Frames come from the DevTools screencast with their
 * timestamps; a cursor overlay and click ripples are drawn in the page so viewers can follow the actions.
 * Elements are found only by accessible role and name, label, or data-testid (see ui-map.mjs).
 */
import { mkdirSync } from 'node:fs';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';
import { now, sleep } from './util.mjs';

export const VIEWPORT = { width: 1280, height: 720 };
export const DSF = 1.5; // frames are 1920x1080 device pixels

const CURSOR_SCRIPT = () => {
  const install = () => {
    if (window.__demoCursor) return;
    const root = document.createElement('div');
    root.setAttribute('popover', 'manual');
    root.setAttribute('aria-hidden', 'true');
    root.dataset.demoOverlay = 'cursor';
    root.style.cssText = 'position:fixed;inset:auto;left:0;top:0;margin:0;padding:0;border:0;background:transparent;'
      + 'overflow:visible;pointer-events:none;width:1px;height:1px;z-index:2147483647;';
    root.innerHTML = '<div data-part="ripple" style="position:absolute;left:-26px;top:-26px;width:52px;height:52px;border-radius:50%;'
      + 'border:3px solid rgba(255,190,90,0.95);box-shadow:0 0 18px rgba(255,170,60,0.7);opacity:0;transform:scale(0.3)"></div>'
      + '<svg data-part="arrow" width="30" height="34" viewBox="0 0 30 34" style="position:absolute;left:-3px;top:-2px;'
      + 'filter:drop-shadow(0 2px 3px rgba(0,0,0,0.55))"><path d="M3 2 L3 27 L9.5 21 L14 31.5 L18.5 29.5 L14 19.5 L23 19.5 Z" '
      + 'fill="#ffffff" stroke="#14102a" stroke-width="2" stroke-linejoin="round"/></svg>';
    document.documentElement.append(root);
    const ripple = root.querySelector('[data-part="ripple"]');
    let x = window.innerWidth * 0.62;
    let y = window.innerHeight * 0.58;
    const place = () => { root.style.transform = `translate(${x}px, ${y}px)`; };
    const raise = () => {
      try { root.hidePopover(); } catch { /* not shown yet */ }
      root.showPopover();
    };
    raise();
    place();
    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
    window.__demoCursor = {
      raise,
      get pos() { return { x, y }; },
      move(tx, ty, ms) {
        raise();
        const sx = x;
        const sy = y;
        const t0 = performance.now();
        return new Promise((resolve) => {
          const step = () => {
            const k = Math.min(1, (performance.now() - t0) / ms);
            const e = ease(k);
            /* a slight arc, like a hand moving a mouse */
            x = sx + (tx - sx) * e;
            y = sy + (ty - sy) * e - Math.sin(Math.PI * e) * Math.min(60, Math.hypot(tx - sx, ty - sy) * 0.08);
            place();
            if (k < 1) requestAnimationFrame(step);
            else resolve();
          };
          requestAnimationFrame(step);
        });
      },
      click() {
        raise();
        ripple.animate([
          { opacity: 0.95, transform: 'scale(0.3)' },
          { opacity: 0, transform: 'scale(1.35)' },
        ], { duration: 520, easing: 'cubic-bezier(0.2, 0.7, 0.3, 1)' });
      },
    };
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
};

export class Screen {
  /** @param {{captureDir: string, scheme?: 'light'|'dark'}} o  scheme is what the page's prefers-color-scheme reports */
  constructor({ captureDir, scheme = 'light' }) {
    if (!['light', 'dark'].includes(scheme)) throw new Error(`Unknown colour scheme ${scheme}`);
    this.scheme = scheme;
    this.dir = path.join(captureDir, 'screen');
    mkdirSync(this.dir, { recursive: true });
    this.frames = [];
    this.dialogs = [];
    this.pageErrors = [];
    this.writes = [];
    this.n = 0;
  }

  async launch() {
    this.browser = await chromium.launch({ headless: true });
    this.context = await this.browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: DSF, locale: 'en-GB',
      timezoneId: 'Europe/London', colorScheme: this.scheme });
    await this.context.addInitScript(CURSOR_SCRIPT);
    this.page = await this.context.newPage();
    this.page.on('pageerror', (e) => this.pageErrors.push({ t: now(), message: String(e) }));
    this.page.on('console', (m) => { if (m.type() === 'error') this.pageErrors.push({ t: now(), message: m.text() }); });
    this.page.on('dialog', async (d) => {
      /* native confirm() dialogs are not part of the screencast, so their real text is logged for a caption */
      const entry = { t: now(), type: d.type(), message: d.message(), accepted: null };
      this.dialogs.push(entry);
      await sleep(1600);
      entry.accepted = this.acceptDialogs !== false;
      entry.answeredAt = now();
      if (entry.accepted) await d.accept();
      else await d.dismiss();
    });
    this.cdp = await this.context.newCDPSession(this.page);
    this.cdp.on('Page.screencastFrame', ({ data, metadata, sessionId }) => {
      const f = `${String(this.n++).padStart(6, '0')}.jpg`;
      this.frames.push({ t: metadata.timestamp * 1000, recv: now(), f });
      this.writes.push(writeFile(path.join(this.dir, f), Buffer.from(data, 'base64')));
      this.cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {});
    });
  }

  async startRecording() {
    await this.cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
    this.recordingFrom = now();
  }

  async stopRecording() {
    await this.cdp.send('Page.stopScreencast');
    await Promise.all(this.writes);
  }

  async goto(url) {
    await this.page.goto(url, { waitUntil: 'domcontentloaded' });
  }

  async _target(locator) {
    await locator.waitFor({ state: 'visible', timeout: 30000 });
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    if (!box) throw new Error(`No bounding box for ${locator}`);
    return { x: box.x + Math.min(box.width / 2, 60 + box.width * 0.25), y: box.y + box.height / 2 };
  }

  async moveTo(locator, { ms = 650 } = {}) {
    const { x, y } = await this._target(locator);
    await Promise.all([
      this.page.evaluate(([px, py, d]) => window.__demoCursor.move(px, py, d), [x, y, ms]),
      this.page.mouse.move(x, y, { steps: 14 }),
    ]);
    return { x, y };
  }

  async click(locator, { ms = 650, before = 220, after = 450 } = {}) {
    await this.moveTo(locator, { ms });
    await sleep(before);
    await this.page.evaluate(() => window.__demoCursor.click());
    await locator.click();
    await sleep(after);
  }

  /** Click into a field and type at a human pace; with replace, select what is there first and type over it. */
  async type(locator, text, { charMs = 38, after = 350, replace = false } = {}) {
    await this.click(locator, { after: 150 });
    if (replace) {
      await this.page.keyboard.press('Control+A');
      await sleep(350);
    }
    await locator.pressSequentially(text, { delay: charMs });
    await sleep(after);
  }

  /** Click into a field and paste (used for the API token, which nobody types). */
  async paste(locator, text, { after = 500 } = {}) {
    await this.click(locator, { after: 250 });
    await locator.fill(text);
    await sleep(after);
  }

  async select(locator, value, { after = 400 } = {}) {
    await this.click(locator, { after: 150 });
    await locator.selectOption(value);
    await sleep(after);
  }

  /** Point at a scrollable area and scroll it with the mouse wheel, gently, like a person reading. */
  async scroll(locator, dy, { steps = 12, stepMs = 70, after = 400 } = {}) {
    await this.moveTo(locator, { ms: 500 });
    for (let i = 0; i < steps; i++) {
      await this.page.mouse.wheel(0, dy / steps);
      await sleep(stepMs);
    }
    await sleep(after);
  }

  async press(key, { after = 300 } = {}) {
    await this.page.keyboard.press(key);
    await sleep(after);
  }

  /** Bounding box in frame pixels, for zooms and focus rectangles in the edit. */
  async box(locator) {
    const b = await locator.boundingBox();
    if (!b) throw new Error(`No bounding box for ${locator}`);
    return { x: b.x * DSF, y: b.y * DSF, w: b.width * DSF, h: b.height * DSF };
  }

  async close() {
    if (this.browser) await this.browser.close();
  }

  manifest() {
    return { dir: 'screen', scheme: this.scheme, frames: this.frames, viewport: VIEWPORT, dsf: DSF, dialogs: this.dialogs,
      pageErrors: this.pageErrors, recordingFrom: this.recordingFrom };
  }
}
