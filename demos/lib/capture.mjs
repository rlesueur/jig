/*
 * Performs the REAL run of one demo scenario and records it.
 *
 *   node lib/capture.mjs <scenario> [--test] [--keep-data]
 *
 * Writes demos/captures/<scenario>/<stamp>/manifest.json plus screen frames, console logs and the event
 * stream. Exit codes: 0 passed, 1 an acceptance check or the run failed, 3 the feature has not landed yet.
 */
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { JigInstance } from './jig.mjs';
import { Screen } from './screen.mjs';
import { TermSession } from './term.mjs';
import { CAPTURES, Checks, DEMOS, WORK, gitHead, now, parseArgs, sleep, venvBin } from './util.mjs';

export class Pending extends Error {
  constructor(message) {
    super(message);
    this.pending = true;
  }
}

/** The commit the demo venv actually runs (prepared by run.ps1), checked against where Jig is imported from. */
function snapshotCommit() {
  const file = path.join(WORK, 'snapshot.txt');
  if (!existsSync(file)) throw new Error('demos/.work/snapshot.txt is missing; run demos/run.ps1, which prepares the snapshot');
  const sha = readFileSync(file, 'utf8').trim();
  const r = spawnSync(venvBin('python.exe'), ['-c', 'import jig, pathlib; print(pathlib.Path(jig.__file__).resolve().parents[1].name)'],
    { encoding: 'utf8', cwd: os.tmpdir() });
  if (r.stdout.trim() !== `src-${sha}`) throw new Error(`The demo venv imports Jig from "${r.stdout.trim()}", not src-${sha}; run demos/run.ps1`);
  return sha;
}

/** The model the real model server is serving (recorded in FINDINGS; never shown in a video). */
async function modelServed() {
  const r = await fetch('http://127.0.0.1:8080/v1/models');
  if (!r.ok) throw new Error(`The model server on 127.0.0.1:8080 answered /v1/models with HTTP ${r.status}`);
  return (await r.json()).data.map((m) => m.id).join(', ');
}

export async function loadScenario(name) {
  const mod = await import(pathToFileURL(path.join(DEMOS, 'scenarios', `${name}.mjs`)).href);
  if (!mod.default?.capture || !mod.default?.edit) throw new Error(`scenarios/${name}.mjs must export default { capture, edit }`);
  return mod.default;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const name = args._[0];
  if (!name) throw new Error('usage: node lib/capture.mjs <scenario> [--test]');
  const scenario = await loadScenario(name);
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const dir = path.join(CAPTURES, name, stamp);
  mkdirSync(dir, { recursive: true });
  console.log(`Capturing ${name} into ${dir}`);

  const checks = new Checks();
  const marks = [];
  const terms = [];
  const notes = [];
  let screen = null;
  const cleanups = [];
  const jig = new JigInstance({ name, captureDir: dir, config: scenario.config || 'demo.toml' });
  const ctx = {
    name, dir, checks, jig, test: Boolean(args.test), sleep, now,
    /** Register clean-up that must run however the capture ends (newest first, after the browser closes). */
    onCleanup(what, fn) { cleanups.unshift({ what, fn }); },
    mark(id, extra = {}) {
      const m = { id, t: now(), ...extra };
      marks.push(m);
      console.log(`  mark  ${id}`);
      return m;
    },
    note(text) {
      notes.push({ t: now(), text });
      console.log(`  note  ${text}`);
    },
    async term(id, opts = {}) {
      const t = new TermSession({ id, captureDir: dir, ...opts, env: jig.env(opts.env || {}) });
      terms.push(t);
      await t.start();
      return t;
    },
    async screen() {
      if (!screen) {
        screen = new Screen({ captureDir: dir, scheme: scenario.scheme || 'light' });
        await screen.launch();
      }
      return screen;
    },
  };

  const manifest = { scenario: name, title: scenario.title, commit: snapshotCommit(), repoHead: gitHead(), startedAt: now(), test: ctx.test,
    host: { os: process.platform }, model: await modelServed() };
  let status = 'passed';
  let error = null;
  try {
    /* a scenario can film a Jig that is already running with its own accounts (see JigInstance.attachExternal) */
    if (scenario.external) await jig.attachExternal(scenario.external);
    else await jig.prepare();
    await scenario.capture(ctx);
  } catch (err) {
    status = err.pending ? 'pending' : 'failed';
    error = err.pending ? err.message : (err.stack || String(err));
    console.error(err.pending ? `PENDING: ${err.message}` : `FAILED: ${error}`);
  } finally {
    if (screen) {
      try { await screen.stopRecording(); } catch { /* not started */ }
      await screen.close();
    }
    for (const c of cleanups) {
      try { await c.fn(); console.log(`  clean ${c.what}`); } catch (err) {
        console.error(`Clean-up "${c.what}" failed: ${err.message}`);
        if (status === 'passed') { status = 'failed'; error = `clean-up "${c.what}" failed: ${err.message}`; }
      }
    }
    try { await jig.stop(); } catch (err) { console.error(`Stopping Jig: ${err.message}`); }
    if (jig.serve && !terms.includes(jig.serve)) terms.push(jig.serve);
    for (const t of terms) {
      try { await t.close(); } catch (err) { console.error(`Closing console ${t.id}: ${err.message}`); }
    }
    manifest.endedAt = now();
    manifest.status = status;
    manifest.error = error;
    manifest.marks = marks;
    manifest.notes = notes;
    manifest.checks = checks.results;
    manifest.terms = terms.map((t) => t.manifest([jig.token]));
    manifest.screen = screen ? screen.manifest() : null;
    manifest.workspace = jig.external ? null : jig.workspace;
    if (jig.external) manifest.external = jig.external;
    manifest.events = jig.events ? 'events.jsonl' : null;
    writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest, null, 2));
    await jig.cleanup({ keep: Boolean(args['keep-data']) || status === 'failed' });
  }
  const passed = checks.results.filter((c) => c.pass === true).length;
  console.log(`\n${name}: ${status.toUpperCase()} (${passed} checks passed) -> ${path.join(dir, 'manifest.json')}`);
  writeFileSync(path.join(CAPTURES, name, 'latest.txt'), stamp);
  process.exit(status === 'passed' ? 0 : status === 'pending' ? 3 : 1);
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
