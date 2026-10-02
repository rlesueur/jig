/*
 * Renders a captured demo run into a video, frame by frame, from the scenario's edit.
 *
 *   node lib/render.mjs <scenario> [--capture=<stamp>|latest] [--format=landscape|portrait|all]
 *                       [--theme=neon] [--no-audio]
 *
 * Output: demos/out/<scenario>/jig-demo-<scenario>-<WxH>.mp4 (with the synthesised bed), a -silent copy,
 * review stills and ffprobe-summary.json. Needs ffmpeg/ffprobe on PATH (or FFMPEG / FFPROBE).
 */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';
import { loadScenario } from './capture.mjs';
import { FORMATS, FPS, formatVars } from './formats.mjs';
import { CAPTURES, DEMOS, LIB, OUT, WORK, parseArgs, run, venvBin } from './util.mjs';

const FFMPEG = process.env.FFMPEG || 'ffmpeg';
const FFPROBE = process.env.FFPROBE || 'ffprobe';
const ORIGIN = 'http://demo.render';
const FONTS = { 'SegUIVar.ttf': 'C:\\Windows\\Fonts\\SegUIVar.ttf', 'bahnschrift.ttf': 'C:\\Windows\\Fonts\\bahnschrift.ttf',
  'CascadiaMono.ttf': 'C:\\Windows\\Fonts\\CascadiaMono.ttf' };
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf',
  '.jpg': 'image/jpeg', '.png': 'image/png', '.jsonl': 'application/x-ndjson', '.json': 'application/json' };

function requireTool(cmd, name) {
  const r = spawnSync(cmd, ['-version'], { encoding: 'utf8' });
  if (r.error || r.status !== 0) throw new Error(`${name} was not found (tried "${cmd}"); demos/run.ps1 puts it on PATH, or set ${name.toUpperCase()}`);
}

const human = (ms) => {
  const s = Math.round(ms / 1000);
  return s >= 90 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`;
};

/** Turn the scenario's edit (source times) into an output-time plan for the composition page. */
export function buildPlan(edit, manifest, formatName) {
  const format = FORMATS[formatName];
  const shots = [];
  const cues = [];
  let out = 0;
  const lastEnd = new Map();
  let prevChapter = null;
  edit.shots.forEach((s, i) => {
    const id = s.id || `${i}`;
    const base = { id, type: s.type, chapter: s.chapter || '', header: s.header || null, outStart: out, fadeIn: s.fadeIn, fadeOut: s.fadeOut };
    let shot;
    if (s.type === 'card') {
      if (!(s.dur > 0)) throw new Error(`card shot ${id} needs a positive dur`);
      shot = { ...base, outDur: s.dur, srcFrom: 0, speed: 1, card: s.card };
      cues.push({ t: out, kind: 'card' });
    } else if (s.type === 'screen' || s.type === 'term') {
      if (!(s.to > s.from)) throw new Error(`shot ${id} has to <= from`);
      const speed = s.speed || 1;
      if (!Number.isInteger(speed) || speed < 1) throw new Error(`shot ${id}: speed must be a whole number >= 1, got ${speed}`);
      const key = s.type === 'screen' ? 'screen' : `term:${s.session}`;
      const prev = lastEnd.get(key);
      if (prev !== undefined && s.from < prev - 50) throw new Error(`shot ${id} goes back in time on ${key}; edits may only move forwards`);
      const gap = prev !== undefined ? s.from - prev : 0;
      const outDur = (s.to - s.from) / 1000 / speed;
      const local = (src) => (src - s.from) / 1000 / speed;
      shot = {
        ...base, outDur, srcFrom: s.from, speed,
        session: s.session, termTitle: s.title,
        skipLabel: gap > 1500 ? `⏭ ${human(gap)} skipped` : '',
        /* a caption ends when the next one starts, so it never describes a moment that has passed */
        captions: (s.captions || []).map((c) => {
          const from = Math.max(0, c.at === undefined ? 0 : local(c.at));
          return { from, to: Math.min(outDur, c.dur ? from + c.dur : outDur), text: c.text };
        }).sort((a, b) => a.from - b.from).map((c, j, all) => ({ ...c, to: Math.min(c.to, all[j + 1] ? all[j + 1].from - 0.05 : c.to) })),
        crops: (s.crops || []).map((c) => ({ at: Math.max(0, local(c.at)), box: c.box, ease: c.ease ?? 0.7 })),
        rings: (s.rings || []).map((r) => ({ from: local(r.from), to: local(r.to), box: r.box })),
        callouts: (s.callouts || []).map((c) => ({ from: local(c.from), to: local(c.to), label: c.label, text: c.text })),
      };
      for (const c of s.sfx || []) cues.push({ t: out + local(c.at), kind: c.kind });
      lastEnd.set(key, s.to);
    } else throw new Error(`Unknown shot type ${s.type}`);
    if (shot.chapter && shot.chapter !== prevChapter) cues.push({ t: out, kind: 'chapter' });
    prevChapter = shot.chapter || prevChapter;
    shots.push(shot);
    out += shot.outDur;
  });
  const terms = {};
  for (const s of shots.filter((x) => x.type === 'term')) {
    if (terms[s.session]) continue;
    const t = manifest.terms.find((x) => x.id === s.session);
    if (!t) throw new Error(`The capture has no console session "${s.session}"`);
    if (!Array.isArray(t.redact)) throw new Error(`Console session "${s.session}" has no redaction record (an older capture); capture it again`);
    terms[s.session] = { log: t.log, cols: t.cols, rows: t.rows, redact: t.redact || [] };
  }
  const screenFrames = manifest.screen ? manifest.screen.frames.map((f) => [f.t, f.f]).sort((a, b) => a[0] - b[0]) : [];
  if (shots.some((s) => s.type === 'screen') && !screenFrames.length) throw new Error('The edit has screen shots but the capture has no screen frames');
  return {
    fps: FPS, format, vars: formatVars(format), shots, terms, screenFrames, duration: out,
    totalFrames: Math.round(out * FPS), cues,
    watermark: manifest.test ? 'Test take · not for publication' : '',
  };
}

function router(captureDir, commit) {
  const avatarDir = path.join(WORK, `src-${commit}`, 'avatar');
  if (!existsSync(avatarDir)) throw new Error(`${avatarDir} is missing; demos/run.ps1 prepares the snapshot of commit ${commit}`);
  return async (route) => {
    const url = new URL(route.request().url());
    const p = decodeURIComponent(url.pathname);
    let file = null;
    if (p.startsWith('/lib/')) file = path.join(LIB, p.slice(5));
    else if (p.startsWith('/themes/')) file = path.join(DEMOS, 'themes', p.slice(8));
    else if (p.startsWith('/cap/')) file = path.join(captureDir, p.slice(5));
    else if (p.startsWith('/xterm/')) file = path.join(DEMOS, 'node_modules', '@xterm', 'xterm', p.slice(7));
    else if (p.startsWith('/avatar/')) file = path.join(avatarDir, p.slice(8));
    else if (p.startsWith('/fonts/')) file = FONTS[p.slice(7)];
    else if (p === '/composition.html') file = path.join(LIB, 'composition.html');
    if (!file || !existsSync(file)) {
      console.error(`404 ${p}`);
      return route.fulfill({ status: 404, body: 'not found' });
    }
    return route.fulfill({ status: 200, body: await readFile(file), contentType: TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream' });
  };
}

async function probe(file) {
  const outp = await run(FFPROBE, ['-v', 'error', '-show_entries',
    'format=duration,size,bit_rate:stream=codec_type,codec_name,profile,width,height,pix_fmt,r_frame_rate,nb_frames,sample_rate,channels',
    '-of', 'json', file]);
  const j = JSON.parse(outp);
  const v = j.streams.find((s) => s.codec_type === 'video');
  const a = j.streams.find((s) => s.codec_type === 'audio');
  return {
    file, duration: Number(j.format.duration).toFixed(3), sizeMB: (Number(j.format.size) / 1048576).toFixed(2),
    video: `${v.codec_name} (${v.profile}) ${v.width}x${v.height} ${v.pix_fmt} ${v.r_frame_rate} fps, ${v.nb_frames} frames`,
    audio: a ? `${a.codec_name} ${a.sample_rate} Hz ${a.channels}ch` : 'none',
  };
}

async function renderFormat(browser, { scenarioName, edit, manifest, captureDir, formatName, theme, outDir }) {
  const plan = buildPlan(edit, manifest, formatName);
  const f = plan.format;
  console.log(`[${formatName}] ${plan.shots.length} shots, ${plan.duration.toFixed(1)} s, ${plan.totalFrames} frames`);
  const context = await browser.newContext({ viewport: { width: f.width, height: f.height }, deviceScaleFactor: 1 });
  await context.route(`${ORIGIN}/**`, router(captureDir, manifest.commit));
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e));
  page.on('console', (msg) => { if (msg.type() === 'error') errors.push(new Error(msg.text())); });
  await page.goto(`${ORIGIN}/composition.html?theme=${theme}`);
  await page.evaluate((p) => window.__demo.load(p), plan);
  if (errors.length) throw errors[0];
  const cdp = await context.newCDPSession(page);

  const name = `jig-demo-${scenarioName}-${f.suffix}`;
  const silent = path.join(outDir, `${name}-silent.mp4`);
  const ff = spawn(FFMPEG, ['-y', '-hide_banner', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'png', '-i', '-',
    '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p', '-c:v', 'libx264', '-preset', 'slow', '-crf', '17',
    '-profile:v', 'high', '-g', String(FPS * 2), '-bf', '2', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
    '-color_range', 'tv', '-movflags', '+faststart', '-r', String(FPS), silent], { stdio: ['pipe', 'ignore', 'pipe'] });
  let ffErr = '';
  ff.stderr.on('data', (d) => (ffErr += d));
  const ffDone = new Promise((resolve, reject) => {
    ff.on('error', reject);
    ff.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`ffmpeg failed (${code}): ${ffErr}`))));
  });

  const stillsDir = path.join(outDir, 'stills');
  mkdirSync(stillsDir, { recursive: true });
  const stillAt = new Map();
  plan.shots.forEach((s, i) => {
    const t = s.type === 'card' ? s.outStart + Math.min(1.6, s.outDur * 0.6) : s.outStart + s.outDur * 0.6;
    stillAt.set(Math.round(t * FPS), `${formatName}-${String(i).padStart(2, '0')}-${s.id}`);
  });
  for (const t of edit.stills || []) stillAt.set(Math.round(t * FPS), `${formatName}-at-${t.toFixed(1).replace('.', '_')}s`);

  const started = Date.now();
  for (let i = 0; i < plan.totalFrames; i++) {
    await page.evaluate((n) => window.__demo.renderFrame(n), i);
    if (errors.length) throw errors[0];
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true });
    const png = Buffer.from(data, 'base64');
    if (!ff.stdin.write(png)) await new Promise((r) => ff.stdin.once('drain', r));
    if (stillAt.has(i)) await writeFile(path.join(stillsDir, `${stillAt.get(i)}.png`), png);
    if (i % 150 === 0) console.log(`[${formatName}] frame ${i}/${plan.totalFrames} (${((i + 1) / ((Date.now() - started) / 1000)).toFixed(1)} frames/s)`);
  }
  ff.stdin.end();
  await ffDone;
  await context.close();
  return { silent, plan, name };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const scenarioName = args._[0];
  if (!scenarioName) throw new Error('usage: node lib/render.mjs <scenario> [--capture=<stamp>] [--format=landscape|portrait|all] [--theme=neon]');
  requireTool(FFMPEG, 'ffmpeg');
  requireTool(FFPROBE, 'ffprobe');
  const scenario = await loadScenario(scenarioName);
  const stamp = !args.capture || args.capture === 'latest'
    ? readFileSync(path.join(CAPTURES, scenarioName, 'latest.txt'), 'utf8').trim() : args.capture;
  const captureDir = path.join(CAPTURES, scenarioName, stamp);
  const manifest = JSON.parse(readFileSync(path.join(captureDir, 'manifest.json'), 'utf8'));
  if (manifest.status !== 'passed') throw new Error(`Capture ${stamp} did not pass (${manifest.status}); refusing to render it`);
  if (args.test) manifest.test = true;
  const theme = args.theme || 'neon';
  if (!existsSync(path.join(DEMOS, 'themes', `${theme}.css`))) throw new Error(`No theme demos/themes/${theme}.css`);
  const formats = args.format === 'all' ? (scenario.formats || ['landscape']) : [args.format || 'landscape'];
  const outDir = path.join(OUT, scenarioName);
  mkdirSync(outDir, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    for (const formatName of formats) {
      if (!FORMATS[formatName]) throw new Error(`Unknown format ${formatName}`);
      const edit = await scenario.edit(manifest, formatName);
      const { silent, plan, name } = await renderFormat(browser, { scenarioName, edit, manifest, captureDir, formatName, theme, outDir });
      results.push(await probe(silent));
      if (!args['no-audio']) {
        const cueFile = path.join(outDir, `${name}-cues.json`);
        const bed = path.join(outDir, `${name}-bed.wav`);
        await writeFile(cueFile, JSON.stringify({ duration: plan.duration, cues: plan.cues }, null, 2));
        console.log((await run(venvBin('python.exe'), [path.join(LIB, 'bed.py'), cueFile, bed])).trim());
        const withAudio = path.join(outDir, `${name}.mp4`);
        await run(FFMPEG, ['-y', '-hide_banner', '-loglevel', 'error', '-i', silent, '-i', bed, '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy',
          '-af', 'loudnorm=I=-20:TP=-2:LRA=9', '-ar', '48000', '-c:a', 'aac', '-b:a', '160k', '-t', plan.duration.toFixed(3),
          '-movflags', '+faststart', withAudio]);
        results.push(await probe(withAudio));
      }
    }
  } finally {
    await browser.close();
  }
  await writeFile(path.join(outDir, 'ffprobe-summary.json'), JSON.stringify({ capture: stamp, commit: manifest.commit, theme, results }, null, 2));
  console.table(results.map((r) => ({ file: path.basename(r.file), duration: r.duration, MB: r.sizeMB, video: r.video, audio: r.audio })));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
