/* Shared paths, process helpers and acceptance assertions for the demo pipeline. */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const LIB = path.dirname(fileURLToPath(import.meta.url));
export const DEMOS = path.resolve(LIB, '..');
export const JIG_REPO = path.resolve(DEMOS, '..');
export const WORK = path.join(DEMOS, '.work');
export const CAPTURES = path.join(DEMOS, 'captures');
export const OUT = path.join(DEMOS, 'out');

/* Ports owned by other agents and the user's model server. A demo must never bind or call them, except
 * the model server, which Jig itself talks to. */
export const FORBIDDEN_PORTS = [8080, 8765, 8766, 8767, 8780, 8790];
export const PORTS = { jig: 8770, site: 8771, nothing: 8779 };
for (const p of Object.values(PORTS)) {
  if (FORBIDDEN_PORTS.includes(p)) throw new Error(`Demo port ${p} is reserved for someone else`);
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const now = () => Date.now();

export function parseArgs(argv) {
  const out = { _: [] };
  for (const a of argv) {
    if (a.startsWith('--')) {
      const [k, v] = a.slice(2).split('=');
      out[k] = v ?? true;
    } else out._.push(a);
  }
  return out;
}

export function portFree(port, host = '127.0.0.1') {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.once('error', () => resolve(false));
    s.once('listening', () => s.close(() => resolve(true)));
    s.listen(port, host);
  });
}

export async function requirePortFree(port) {
  if (!(await portFree(port))) throw new Error(`Port ${port} is already in use; stop whatever is listening there first`);
}

export function requireFile(file, hint) {
  if (!existsSync(file)) throw new Error(`${file} is missing${hint ? `: ${hint}` : ''}`);
  return file;
}

export function venvBin(name) {
  return requireFile(path.join(WORK, 'venv', 'Scripts', name), 'run demos/run.ps1, which prepares the demo venv');
}

export function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true, ...opts });
    let out = '';
    let err = '';
    p.stdout.on('data', (d) => (out += d));
    p.stderr.on('data', (d) => (err += d));
    p.on('error', reject);
    p.on('close', (code) => (code === 0 ? resolve(out) : reject(new Error(`${cmd} ${args.join(' ')} exited with ${code}\n${err.slice(-3000)}`))));
  });
}

/** The clean `git archive` of the commit under test, prepared by run.ps1 (the tree the demo venv runs). */
export function snapshotDir() {
  const file = path.join(WORK, 'snapshot.txt');
  requireFile(file, 'run demos/run.ps1, which prepares the snapshot');
  return path.join(WORK, `src-${readFileSync(file, 'utf8').trim()}`);
}

export function gitHead() {
  const r = spawnSync('git', ['-C', JIG_REPO, 'rev-parse', '--short', 'HEAD'], { encoding: 'utf8' });
  if (r.status !== 0) throw new Error(`git rev-parse failed: ${r.stderr}`);
  return r.stdout.trim();
}

/** Acceptance assertions. Every check is recorded; the first failure stops the run. */
export class Checks {
  constructor() {
    this.results = [];
  }

  ok(name, condition, detail = '') {
    const pass = Boolean(condition);
    this.results.push({ name, pass, detail: typeof detail === 'string' ? detail : JSON.stringify(detail), t: now() });
    console.log(`  ${pass ? 'PASS' : 'FAIL'}  ${name}${detail && !pass ? ` — ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
    if (!pass) throw new AcceptanceError(name, detail);
  }

  equal(name, actual, expected) {
    this.ok(name, actual === expected, `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }

  note(name, detail) {
    this.results.push({ name, pass: null, detail: typeof detail === 'string' ? detail : JSON.stringify(detail), t: now() });
    console.log(`  NOTE  ${name}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
  }
}

export class AcceptanceError extends Error {
  constructor(name, detail) {
    super(`Acceptance check failed: ${name}${detail ? ` — ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
    this.check = name;
  }
}

/** Poll until fn() returns a truthy value; throw with a clear message on timeout. */
export async function waitUntil(what, fn, { timeoutMs = 60000, intervalMs = 400 } = {}) {
  const deadline = now() + timeoutMs;
  let last;
  for (;;) {
    last = await fn();
    if (last) return last;
    if (now() > deadline) throw new Error(`Timed out after ${Math.round(timeoutMs / 1000)} s waiting for ${what}`);
    await sleep(intervalMs);
  }
}
