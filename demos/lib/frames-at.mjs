/*
 * Review helper: copy the captured screen frame at each mark (or at mark+offset ms) to demos/out/<scenario>/review/.
 *   node lib/frames-at.mjs <scenario> [--capture=<stamp>] [mark[+ms]] ...   (no marks: every mark)
 */
import { copyFileSync, mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { CAPTURES, OUT, parseArgs } from './util.mjs';

const args = parseArgs(process.argv.slice(2));
const [name, ...wanted] = args._;
const stamp = !args.capture || args.capture === 'latest' ? readFileSync(path.join(CAPTURES, name, 'latest.txt'), 'utf8').trim() : args.capture;
const dir = path.join(CAPTURES, name, stamp);
const m = JSON.parse(readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
const frames = m.screen.frames.slice().sort((a, b) => a.t - b.t);
const at = (t) => frames.reduce((best, f) => (f.t <= t ? f : best), frames[0]);
const out = path.join(OUT, name, 'review');
mkdirSync(out, { recursive: true });
const specs = wanted.length ? wanted : m.marks.map((x) => x.id);
for (const spec of specs) {
  const [id, off = '0'] = spec.split('+');
  const mk = m.marks.find((x) => x.id === id);
  if (!mk) throw new Error(`no mark ${id}`);
  const f = at((mk.at ?? mk.t) + Number(off));
  const file = path.join(out, `${id}${off !== '0' ? `+${off}` : ''}.jpg`);
  copyFileSync(path.join(dir, 'screen', f.f), file);
  console.log(file);
}
