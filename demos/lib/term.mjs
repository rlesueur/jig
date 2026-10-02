/* A real console session (ConPTY via ptybridge.py). Its exact output is logged with timestamps for replay. */
import { spawn } from 'node:child_process';
import path from 'node:path';
import { JIG_REPO, LIB, now, sleep, venvBin, waitUntil } from './util.mjs';

const ANSI = /\x1b\[[0-?]*[ -\/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g;
export const stripAnsi = (s) => s.replace(ANSI, '');

/* The prompt carries an invisible OSC marker with the last exit code, so we know exactly when a
 * command has finished and whether it succeeded. xterm.js ignores unknown OSC sequences. */
const MARKER = /\x1b\]9001;P;(-?\d+)\x07/g;
const PROMPT_TEXT = 'PS Jig> ';

export class TermSession {
  /**
   * @param {object} o
   * @param {string} o.id           used in file names and to refer to the session in the edit
   * @param {string} o.captureDir
   * @param {object} o.env
   * @param {string[]} [o.argv]     defaults to an interactive Windows PowerShell
   */
  constructor({ id, captureDir, env, argv = null, cols = 112, rows = 30, cwd = JIG_REPO, title = '' }) {
    this.id = id;
    this.captureDir = captureDir;
    this.env = env;
    this.shell = !argv;
    this.argv = argv || ['powershell.exe', '-NoLogo', '-NoProfile'];
    this.cols = cols;
    this.rows = rows;
    this.cwd = cwd;
    this.title = title;
    this.log = path.join(captureDir, `term-${id}.jsonl`);
    this.raw = '';
    this.chunks = [];
    this.listeners = [];
    this.exited = false;
    this.exitCode = null;
    this.prompts = [];
    this.clearedAt = null;
    this.commands = [];
  }

  start() {
    this.proc = spawn(venvBin('python.exe'), [path.join(LIB, 'ptybridge.py'), '--cols', String(this.cols), '--rows',
      String(this.rows), '--log', this.log, '--cwd', this.cwd, '--', ...this.argv],
    { env: this.env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    let buf = '';
    this.proc.stdout.setEncoding('utf8');
    this.proc.stdout.on('data', (d) => {
      buf += d;
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 1);
        if (line.trim()) this._onMessage(JSON.parse(line));
      }
    });
    let err = '';
    this.proc.stderr.on('data', (d) => (err += d));
    this.proc.on('close', (code) => {
      if (!this.exited) {
        this.exited = true;
        this.exitCode = code;
        if (code !== 0) console.error(`ptybridge for ${this.id} exited with ${code}: ${err.slice(-2000)}`);
      }
    });
    return this.shell ? this._setupShell() : Promise.resolve();
  }

  _onMessage(m) {
    if ('data' in m) {
      const t = m.t * 1000;
      this.chunks.push({ t, data: m.data });
      this.raw += m.data;
      /* ConPTY asks the terminal to identify itself and waits for an answer, as a real terminal would give */
      if (m.data.includes('\x1b[c')) this._send('\x1b[?1;0c');
      if (m.data.includes('\x1b[6n')) this._send('\x1b[1;1R');
      for (const match of m.data.matchAll(MARKER)) this.prompts.push({ t, code: Number(match[1]) });
      for (const l of this.listeners) l(m.data);
    } else if ('exit' in m) {
      this.exited = true;
      this.exitCode = m.exit;
    }
  }

  _send(data) {
    if (this.exited) throw new Error(`console ${this.id} has already exited`);
    this.proc.stdin.write(`${JSON.stringify({ op: 'write', data })}\n`);
  }

  onData(cb) {
    this.listeners.push(cb);
  }

  async _setupShell() {
    await sleep(600);
    const setup = [
      `function global:prompt { $c = $global:LASTEXITCODE; if ($null -eq $c) { $c = 0 }; $global:LASTEXITCODE = 0; "$([char]27)]9001;P;$c$([char]7)${PROMPT_TEXT}" }`,
      'try { Set-PSReadLineOption -PredictionSource None } catch { }',
      /* the pseudo-console starts PowerShell in a new process group, which disables Ctrl+C for it and
       * everything it launches; re-enable it so Ctrl+C stops `jig serve` the way it would for a person */
      `Add-Type -Name K32 -Namespace Demo -MemberDefinition '[DllImport("kernel32.dll")] public static extern bool SetConsoleCtrlHandler(System.IntPtr h, bool add);'`,
      '[void][Demo.K32]::SetConsoleCtrlHandler([System.IntPtr]::Zero, $false)',
      `Set-Location '${this.cwd}'`,
      `$Host.UI.RawUI.WindowTitle = 'Jig'`,
    ].join('; ');
    this._send(`${setup}\r`);
    await waitUntil(`the ${this.id} console to be ready`, () => this.prompts.length > 0, { timeoutMs: 30000 });
    const before = this.prompts.length;
    this._send('Clear-Host\r');
    await waitUntil(`the ${this.id} console to clear`, () => this.prompts.length > before, { timeoutMs: 15000 });
    await sleep(300);
    this.clearedAt = now();
  }

  /** Type keystrokes at a human pace. */
  async type(text, { charMs = 42 } = {}) {
    let i = 0;
    for (const ch of text) {
      this._send(ch);
      i += 1;
      /* deterministic, slightly uneven rhythm */
      await sleep(Math.max(1, charMs + ((i * 37) % 23) - 11));
    }
  }

  /**
   * Type a command, press Enter and (unless wait is false) wait for the prompt to come back.
   * Returns { cmd, start, end, code, output }. Throws if the exit code differs from `expectCode`.
   */
  async typeCommand(cmd, { wait = true, timeoutMs = 300000, expectCode = 0, pauseAfterMs = 900, charMs = 42, answers = [] } = {}) {
    if (!this.shell) throw new Error('typeCommand needs an interactive shell session');
    const start = now();
    const promptsBefore = this.prompts.length;
    const rawBefore = this.raw.length;
    await this.type(cmd, { charMs });
    await sleep(250);
    this._send('\r');
    const entry = { cmd, start, enter: now(), end: null, code: null, answers: [] };
    this.commands.push(entry);
    /* answers: [{ when: /question/, send: 'y', readMs }] — typed only once the real question is on screen */
    for (const a of answers) {
      await this.expect(a.when, { from: rawBefore, timeoutMs, what: `the question ${a.when}` });
      const asked = now();
      await sleep(a.readMs ?? 2500);
      await this.type(a.send, { charMs: 160 });
      await sleep(300);
      this._send('\r');
      entry.answers.push({ when: String(a.when), send: a.send, asked, answered: now() });
    }
    if (!wait) return entry;
    const p = await waitUntil(`"${cmd}" to finish`, () => this.prompts[promptsBefore], { timeoutMs, intervalMs: 100 });
    entry.end = p.t;
    entry.code = p.code;
    entry.output = stripAnsi(this.raw.slice(rawBefore));
    if (expectCode !== null && entry.code !== expectCode) {
      throw new Error(`"${cmd}" exited with ${entry.code}, expected ${expectCode}. Output:\n${entry.output.slice(-2500)}`);
    }
    await sleep(pauseAfterMs);
    return entry;
  }

  /** Wait until the plain-text output (from offset `from`) matches re. */
  async expect(re, { from = 0, timeoutMs = 120000, what = String(re) } = {}) {
    return waitUntil(what, () => {
      const m = stripAnsi(this.raw.slice(from)).match(re);
      return m || (this.exited ? (() => { throw new Error(`console ${this.id} exited before ${what}`); })() : null);
    }, { timeoutMs, intervalMs: 120 });
  }

  plainText() {
    return stripAnsi(this.raw);
  }

  /**
   * Note every place `secret` appears in the captured output, so the replay can blank exactly those
   * characters. The secret itself is never written to the manifest.
   */
  redact(secret) {
    this.redactions = this.redactions || [];
    let i = this.raw.indexOf(secret);
    if (i < 0) throw new Error(`console ${this.id}: the secret to redact does not appear in the output`);
    while (i >= 0) {
      this.redactions.push({ start: i, length: secret.length });
      i = this.raw.indexOf(secret, i + secret.length);
    }
    return this.redactions.length;
  }

  async interrupt() {
    if (!this.exited) this._send('\x03');
  }

  async close() {
    if (this.exited) return;
    if (this.shell) {
      this._send('exit\r');
      await waitUntil(`console ${this.id} to exit`, () => this.exited, { timeoutMs: 15000 }).catch(() => this.kill());
    } else {
      await this.kill();
    }
  }

  async kill() {
    if (this.exited) return;
    this.proc.stdin.write(`${JSON.stringify({ op: 'kill' })}\n`);
    await waitUntil(`console ${this.id} to be killed`, () => this.exited, { timeoutMs: 10000 });
  }

  /** Character ranges [start, end) where any of the secrets appear in the concatenated output. */
  secretSpans(secrets) {
    const spans = (this.redactions || []).map((r) => [r.start, r.start + r.length]);
    for (const s of secrets) {
      if (!s) continue;
      for (let i = this.raw.indexOf(s); i >= 0; i = this.raw.indexOf(s, i + s.length)) spans.push([i, i + s.length]);
    }
    return spans;
  }

  /** The secrets themselves are never written to the manifest, only where they were. */
  manifest(secrets = []) {
    return { id: this.id, log: path.basename(this.log), cols: this.cols, rows: this.rows, clearedAt: this.clearedAt,
      title: this.title, commands: this.commands.map(({ output, ...c }) => c), redact: this.secretSpans(secrets) };
  }
}
