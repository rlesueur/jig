/* Our own Jig instance: environment, start-up, the authenticated API and the live event stream. */
import { spawnSync } from 'node:child_process';
import { cpSync, createWriteStream, existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs';
import { rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import WebSocket from 'ws';
import { DEMOS, JIG_REPO, PORTS, WORK, now, requirePortFree, sleep, venvBin, waitUntil } from './util.mjs';
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
    this.base = BASE;
    this.external = null;
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
    /* Windows names it Path; a second PATH key would leave the child with only one of the two */
    const base = Object.fromEntries(Object.entries(process.env).filter(([k]) => k.toLowerCase() !== 'path'));
    return {
      ...base,
      Path: `${scripts};${process.env.PATH}`,
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
    if (this.external) return;
    await requirePortFree(PORTS.jig);
    mkdirSync(this.dataDir, { recursive: true });
    mkdirSync(this.sandboxDir, { recursive: true });
  }

  /** Put the files of demos/fixtures/<name> into Jig's workspace, as a user would before asking. */
  seed(name) {
    const from = path.join(DEMOS, 'fixtures', name);
    if (!existsSync(from)) throw new Error(`No fixture folder ${from}`);
    mkdirSync(this.workspace, { recursive: true });
    cpSync(from, this.workspace, { recursive: true });
    return readdirSync(from);
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
    /* off camera, --no-browser: in a real console on Windows, `jig serve` opens Jig's own window */
    const offCamera = term.id === 'serve';
    await term.typeCommand(offCamera ? 'jig serve --no-browser' : 'jig serve', { wait: false, charMs: offCamera ? 5 : 42 });
    const log = createWriteStream(path.join(this.captureDir, 'jig-serve.log'));
    this.serve.onData((d) => log.write(d));
    await waitUntil('Jig to answer /health (start-up runs the real capability check)', async () => {
      if (this.serve.exited) {
        throw new Error(`jig serve exited during start-up (code ${this.serve.exitCode}). Last output:\n${this.serve.plainText().slice(-2500)}`);
      }
      try {
        const r = await fetch(`${this.base}/health`);
        return r.ok && (await r.json()).status === 'ok';
      } catch {
        return false;
      }
    }, { timeoutMs, intervalMs: 500 });
    this.token = this.readTokenViaCli();
    this.events = new EventRecorder(path.join(this.captureDir, 'events.jsonl'), this.token, this.base);
    await this.events.connect();
  }

  /** Use a Jig that something else started on our port (the Compose deployment), with the token it printed. */
  async attach(token) {
    if (!/^[A-Za-z0-9_-]{32,}$/.test(token || '')) throw new Error('attach() needs the token exactly as `jig token show` printed it');
    this.token = token;
    await this.get('/status');
    this.events = new EventRecorder(path.join(this.captureDir, 'events.jsonl'), this.token, this.base);
    await this.events.connect();
  }

  /**
   * Use a Jig that is already running elsewhere with its own config and data (the connectors test Jig, whose
   * accounts are connected). It runs the repository's working tree, so this refuses unless jig/ has no
   * uncommitted changes and the server started after the last commit to jig/: what is filmed is exactly HEAD.
   * dataDir: the Jig's data folder when it isn't the config's own (it was started with JIG_DATA_DIR).
   */
  async attachExternal({ base, config, dataDir = null, snapshot = false }) {
    const git = (...a) => spawnSync('git', ['-C', JIG_REPO, ...a], { encoding: 'utf8' }).stdout.trim();
    if (snapshot) {
      /* a Jig run from the demo venv (run.ps1's snapshot): its jig/ must be HEAD's, file for file */
      const sha = readFileSync(path.join(WORK, 'venv-commit.txt'), 'utf8').trim();
      if (git('rev-parse', `${sha}:jig`) !== git('rev-parse', 'HEAD:jig')) {
        throw new Error(`The demo venv runs ${sha}, whose jig/ differs from HEAD's; run demos/run.ps1 -PrepareOnly and restart ${base}`);
      }
    } else {
      const dirty = git('status', '--porcelain', '--', 'jig');
      if (dirty) throw new Error(`jig/ has uncommitted changes, which would end up on camera:\n${dirty}`);
    }
    /* the last commit that changed Jig itself; a later commit to tests or demos needs no restart */
    const head = spawnSync('git', ['-C', JIG_REPO, 'log', '-1', '--format=%h %ct', '--', 'jig'], { encoding: 'utf8' }).stdout.trim().split(' ');
    this.base = base;
    this.configPath = config;
    this.external = { base, config, dataDir, snapshot, head: head[0] };
    const repoJig = path.join(JIG_REPO, '.venv', 'Scripts', 'jig.exe');
    const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('JIG_')));
    if (dataDir) env.JIG_DATA_DIR = dataDir;
    const r = spawnSync(repoJig, ['--config', config, 'token', 'show'], { env, encoding: 'utf8', windowsHide: true });
    if (r.status !== 0) throw new Error(`jig token show failed (${r.status}): ${r.stderr}`);
    this.token = r.stdout.trim().split(/\r?\n/).pop().trim();
    const starts = (await this.get('/audit?kind=runtime.start&limit=1000')).filter((a) => a.kind === 'runtime.start');
    const started = Math.max(...starts.map((a) => Date.parse(a.ts))) / 1000;
    if (!(started >= Number(head[1]))) {
      throw new Error(`The Jig at ${base} started before commit ${head[0]}; restart it so it runs exactly HEAD`);
    }
    this.events = new EventRecorder(path.join(this.captureDir, 'events.jsonl'), this.token, this.base);
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
    const r = await fetch(`${this.base}${route}`, {
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
    if (this.external) return;
    if (keep) {
      console.log(`Kept Jig's temporary data in ${this.root}`);
      return;
    }
    if (existsSync(this.root)) await rm(this.root, { recursive: true, force: true });
  }
}

/** Every event from the real /events WebSocket, timestamped on receipt and saved to disk. */
export class EventRecorder {
  constructor(file, token, base = BASE) {
    this.file = file;
    this.token = token;
    this.base = base;
    this.events = [];
    this.out = createWriteStream(file);
    this.ws = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`${this.base.replace('http', 'ws')}/events`, { headers: { Authorization: `Bearer ${this.token}` } });
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
