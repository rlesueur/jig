/* WCAG contrast of the new mockup text against its effective background, both themes. Writes contrast-report.json. */
import { createRequire } from 'node:module';
import { writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';

const require = createRequire('C:/Users/you/Jig/demos/package.json');
const { chromium } = require('playwright');

const server = spawn(process.execPath, ['render.mjs', 'serve'], { cwd: import.meta.dirname, stdio: ['ignore', 'pipe', 'inherit'] });
await new Promise((r) => server.stdout.once('data', r));

const SELECTORS = ['.say', '.b-live', '.b-sub', '.work-line', '.meter-text .pass', '.meter-text .fail', '.count', '.step-title', '.step.now .step-title',
  '.step-sub', '.step-toggle', '.pill.ok', '.pill.bad', '.cmd-label', '.cmd-line', '.cmd-why', '.r-name', '.r.bad .r-name', '.r-id', '.r-why',
  '.change-plain', '.diff-head', '.dl.ctx .dt', '.dl.add .dt', '.dl.del .dt', '.dn', '.waiting-pill', '.glance-h', '.glance-t', '.glance-s',
  '.corner-tip', '.done-t', '.done-s', '.tab[aria-selected="true"]', '.tab[aria-selected="false"]', '.spot-say', '.spot-note', '.c-hello', '.tag', '.setup-steps li'];
const PAGES = ['a:home', 'a:approval', 'a:work', 'a:work-open', 'a:work-tests', 'a:setup', 'a:success', 'b:work', 'b:approval', 'b:home', 'c:home', 'c:approval', 'c:success', 'c:work-open'];

const browser = await chromium.launch({ headless: true });
const report = {};
for (const theme of ['light', 'dark']) {
  const rows = new Map();
  for (const pg of PAGES) {
    const [dir, screen] = pg.split(':');
    const page = await (await browser.newContext({ viewport: { width: 1200, height: 800 } })).newPage();
    await page.goto(`http://127.0.0.1:8823/mock/mock.html?dir=${dir}&screen=${screen}&theme=${theme}&size=desktop`);
    await page.waitForFunction(() => window.__ready === true);
    const found = await page.evaluate((sels) => {
      const parse = (c) => {
        const m = c.match(/[\d.]+/g).map(Number);
        if (c.startsWith('color(srgb')) return { r: m[0] * 255, g: m[1] * 255, b: m[2] * 255, a: m[3] ?? 1 };
        return { r: m[0], g: m[1], b: m[2], a: m[3] ?? 1 };
      };
      const lum = ({ r, g, b }) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
      const blend = (top, bottom) => ({ r: top.r * top.a + bottom.r * (1 - top.a), g: top.g * top.a + bottom.g * (1 - top.a), b: top.b * top.a + bottom.b * (1 - top.a), a: 1 });
      function bgOf(el) {
        const layers = [];
        for (let n = el; n; n = n.parentElement) {
          const cs = getComputedStyle(n);
          if (cs.backgroundImage !== 'none' && n !== document.body && n !== document.documentElement && !cs.backgroundImage.includes('radial')) return { unknown: cs.backgroundImage.slice(0, 60) };
          const c = parse(cs.backgroundColor);
          if (c.a > 0) layers.push(c);
          if (c.a >= 1) break;
        }
        let out = parse(getComputedStyle(document.body).backgroundColor);
        if (out.a < 1) out = { r: 255, g: 255, b: 255, a: 1 };
        for (const l of layers.reverse()) out = blend(l, out);
        return out;
      }
      const res = [];
      for (const sel of sels) {
        const el = document.querySelector(sel);
        if (!el || !el.textContent.trim()) continue;
        const cs = getComputedStyle(el);
        const fg = parse(cs.color);
        const bg = bgOf(el);
        if (bg.unknown) { res.push({ sel, note: `gradient background ${bg.unknown}` }); continue; }
        const opacity = [...function* () { for (let n = el; n; n = n.parentElement) yield Number(getComputedStyle(n).opacity); }()].reduce((a, b) => a * b, 1);
        const f = blend({ ...fg, a: fg.a * opacity }, bg);
        const [l1, l2] = [lum(f), lum(bg)].sort((a, b) => b - a);
        const size = parseFloat(cs.fontSize);
        const weight = Number(cs.fontWeight);
        const large = size >= 24 || (size >= 18.66 && weight >= 700);
        res.push({ sel, ratio: Math.round(((l1 + 0.05) / (l2 + 0.05)) * 100) / 100, size, weight, need: large ? 3 : 4.5 });
      }
      return res;
    }, SELECTORS);
    for (const r of found) {
      const key = r.sel;
      const prev = rows.get(key);
      if (!prev || (r.ratio ?? 99) < (prev.ratio ?? 99)) rows.set(key, { ...r, page: pg });
    }
    await page.context().close();
  }
  report[theme] = [...rows.values()].map((r) => ({ ...r, pass: r.ratio === undefined ? null : r.ratio >= r.need }));
}
await browser.close();
server.kill();
writeFileSync('C:/Users/you/Jig/design/ui-review/contrast-report.json', JSON.stringify(report, null, 1));
for (const theme of Object.keys(report)) {
  const fails = report[theme].filter((r) => r.pass === false);
  const min = Math.min(...report[theme].filter((r) => r.ratio).map((r) => r.ratio));
  console.log(theme, 'checked', report[theme].length, 'lowest', min, 'fails', JSON.stringify(fails));
  const notes = report[theme].filter((r) => r.note);
  if (notes.length) console.log(theme, 'not measured', JSON.stringify(notes.map((n) => n.sel)));
}
