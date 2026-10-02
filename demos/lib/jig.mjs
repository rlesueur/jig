/* Our own Jig instance: environment, start-up, the authenticated API and the live event stream. */
import { spawnSync } from 'node:child_process';
import { createWriteStream, existsSync, mkdirSync, readFileSync } from 'node:fs';
import { rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import WebSocket from 'ws';
import { DEMOS, PORTS, WORK, now, requirePortFree, sleep, venvBin, waitUntil } from './util.mjs';
import { TermSession } from './term.mjs';

export const BASE = `http://127.0.0.1:${PORTS.jig}`;

export class JigInstance {
  /**
   * @param {object} o
   * @param {string} o.name   scenario name, used for the temporary folder
   * @param {string} o.captureDir
   * @param {string} [o.config] config file under demos/configs
   */
  constructor({ name, captureDir, config = 'demo.toml' }) {
    this.name = name;
    this.captureDir = captureDir;
    this.configPath = path.join(DEMOS, 'configs', config);
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    this.root = path.join(os.tmpdir(), 'jig-demos', `${name}-${stamp}`);
    this.dataDir = path.join(this.root, 'data');
    this.sandboxDir = path.join(this.root, 'sandbox');
    this.serve = null;
    this.token = null;
    this.events = null;
  }

  /** Environment for every Jig command in this demo, real CLI and server alike. */
  env(extra = {}) {
    const scripts = path.dirname(venvBin('jig.exe'));
    return {
      ...process.env,
      PATH: `${scripts};${process.env.PATH}`,
      JIG_CONFIG: this.configPath,
      JIG_DATA_DIR: this.dataDir,
      JIG_SANDBOX_DIR: this.sandboxDir,
      JIG_PORT: String(PORTS.jig),
      PYTHONUNBUFFERED: '1',
      PYTHONIOENCODING: 'utf-8',
      ...extra,
    };
  }

  /** The workspace folder Jig's file tools write into. */
  get workspace() {
    return path.join(this.sandboxDir, 'demo');
  }

  async prepare() {
    await requirePortFree(PORTS.jig);
    mkdirSync(this.dataDir, { recursive: true });
    mkdirSync(this.sandboxDir, { recursive: true });
  }

  /**
   * Start `jig serve` inside a real console, so it can be stopped gracefully with Ctrl+C.
   * Pass a TermSession that is already showing PowerShell to start it on camera instead.
   */
  async start({ term = null, timeoutMs = 240000 } = {}) {
    if (!term) {
      term = new TermSession({ id: 'serve', captureDir: this.captureDir, env: this.env(), cols: 140, rows: 40 });
      await term.start();
    }
    this.serve = term;
    await term.typeCommand('jig serve', { wait: false, charMs: term.id === 'serve' ? 5 : 42 });
    const log = createWriteStream(path.join(this.captureDir, 'jig-serve.log'));
    this.serve.onData((d) => log.write(d));
    await waitUntil('Jig to answer /health (start-up runs the real capability check)', async () => {
      if (this.serve.exited) {
        throw new Error(`jig serve exited during start-up (code ${this.serve.exitCode}). Last output:\n${this.serve.plainText().slice(-2500)}`);
      }
      try {
        const r = await fetch(`${BASE}/health`);
        return r.ok && (await r.json()).status === 'ok';
      } catch {
        return false;
      }
    }, { timeoutMs, intervalMs: 500 });
    this.token = this.readTokenViaCli();
    this.events = new EventRecorder(path.join(this.captureDir, 'events.jsonl'), this.token);
    await this.events.connect();
  }

  /** Use a Jig that something else started on our port (the Compose deployment), with the token it printed. */
  async attach(token) {
    if (!/^[A-Za-z0-9_-]{32,}$/.test(token || '')) throw new Error('attach() needs the token exactly as `jig token show` printed it');
    this.token = token;
    await this.get('/status');
    this.events = new EventRecorder(path.join(this.captureDir, 'events.jsonl'), this.token);
    await this.events.connect();
  }

  /** The token exactly as `jig token show` prints it. */
  readTokenViaCli() {
    const r = spawnSync(venvBin('jig.exe'), ['token', 'show'], { env: this.env(), encoding: 'utf8', windowsHide: true });
    if (r.status !== 0) throw new Error(`jig token show failed (${r.status}): ${r.stderr}`);
    const token = r.stdout.trim();
    if (!/^[A-Za-z0-9_-]{32,}$/.test(token)) throw new Error('jig token show printed something that does not look like a token');
    return token;
  }

  async api(method, route, body) {
    const r = await fetch(`${BASE}${route}`, {
      method,
      headers: { Authorization: `Bearer ${this.token}`, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await r.text();
    const data = text ? JSON.parse(text) : null;
    if (!r.ok) throw new Error(`${method} ${route} -> HTTP ${r.status}: ${text.slice(0, 500)}`);
    return data;
  }

  get(route) { return this.api('GET', route); }
  post(route, body = {}) { return this.api('POST', route, body); }
  patch(route, body) { return this.api('PATCH', route, body); }

  async stop() {
    if (this.events) this.events.close();
    if (this.serve && this.serve.shell && !this.serve.exited) {
      /* on camera: Ctrl+C in the PowerShell that runs `jig serve`, then wait for the prompt to return */
      const prompts = this.serve.prompts.length;
      await this.serve.interrupt();
      await waitUntil('jig serve to stop after Ctrl+C', () => this.serve.prompts.length > prompts, { timeoutMs: 30000 })
        .catch(async (err) => {
          console.error(`${err.message}; terminating the console`);
          await this.serve.kill();
        });
    } else if (this.serve && !this.serve.exited) {
      await this.serve.interrupt();
      const deadline = now() + 30000;
      while (!this.serve.exited && now() < deadline) await sleep(200);
      if (!this.serve.exited) {
        console.error('jig serve did not stop within 30 s of Ctrl+C; terminating it');
        await this.serve.kill();
      }
    }
  }

  async cleanup({ keep = false } = {}) {
    if (keep) {
      console.log(`Kept Jig's temporary data in ${this.root}`);
      return;
    }
    if (existsSync(this.root)) await rm(this.root, { recursive: true, force: true });
  }
}

/** Every event from the real /events WebSocket, timestamped on receipt and saved to disk. */
export class EventRecorder {
  constructor(file, token) {
    this.file = file;
    this.token = token;
    this.events = [];
    this.out = createWriteStream(file);
    this.ws = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`${BASE.replace('http', 'ws')}/events`, { headers: { Authorization: `Bearer ${this.token}` } });
      this.ws = ws;
      ws.once('open', () => resolve());
      ws.once('error', reject);
      ws.on('message', (raw) => {
        const e = { t: now(), ...JSON.parse(raw.toString()) };
        this.events.push(e);
        this.out.write(`${JSON.stringify(e)}\n`);
      });
      ws.on('close', (code) => {
        if (!this.closing) console.error(`Event stream closed unexpectedly (code ${code})`);
      });
    });
  }

  /** Wait for an event (received at or after `since`) that matches pred. */
  async waitFor(what, pred, { since = 0, timeoutMs = 300000 } = {}) {
    return waitUntil(what, () => this.events.find((e) => e.t >= since && pred(e)), { timeoutMs, intervalMs: 150 });
  }

  avatarStates(since = 0) {
    return this.events.filter((e) => e.type === 'avatar.state' && e.t >= since).map((e) => ({ t: e.t, ...e.data }));
  }

  close() {
    this.closing = true;
    if (this.ws) this.ws.close();
    this.out.end();
  }
}

export function readJsonl(file) {
  return readFileSync(file, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
}

export { WORK };
