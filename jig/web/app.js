/*
 * Jig web UI. Dependency-free; talks only to the Jig runtime that serves it.
 * Authentication is a same-origin HttpOnly session cookie, so this script never sees the API token.
 * Model output and web-derived text are always inserted as text, never as HTML.
 *
 * The main screen is Jig and the conversation. Approvals appear in the conversation, background work is one
 * line ("What Jig's up to") with the detail a tap away, and everything else lives in Settings (#settings/...).
 */
import { STATES } from '/avatar/jig-avatar.js';

const $ = (id) => document.getElementById(id);
const TERMINAL_TASK = new Set(['done', 'failed', 'cancelled', 'blocked']);
const TERMINAL_GOAL = new Set(['done', 'failed', 'cancelled']);

/* ---------- small DOM helpers ---------- */

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'text') node.textContent = v;
    else if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k in node && typeof v !== 'string') node[k] = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const STATUS_WORDS = {
  waiting_approval: 'waiting for your OK', pending: 'waiting for you', ask_user: 'ask you first', ask: 'ask first',
  interrupted: 'interrupted', planning: 'planning',
};
const statusBadge = (s) => el('span', { class: 'status', dataset: { s }, text: STATUS_WORDS[s] || s.replace(/_/g, ' ') });
const when = (iso) => (iso ? new Date(iso).toLocaleString('en-GB') : '—');
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '');
const json = (v) => JSON.stringify(v, null, 2);
const q = (v) => `\u201c${v}\u201d`;
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const empty = (text, more) => el('div', { class: 'empty-state' },
  el('p', { class: 'empty', text }), more ? el('p', { class: 'hint', text: more }) : null);

/* ---------- errors: always visible ---------- */

function showError(message) {
  const box = $('errors');
  const toast = el('div', { class: 'toast', 'data-testid': 'error-toast' },
    el('div', { class: 'toast-body' },
      el('p', { class: 'toast-title', text: 'Sorry, that didn\u2019t work' }),
      el('p', { class: 'toast-detail', text: message })),
    el('button', { type: 'button', text: 'Dismiss', onclick: () => toast.remove() }));
  box.append(toast);
  while (box.children.length > 5) box.firstElementChild.remove();
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function errorText(data, status) {
  if (data && typeof data.error === 'string') return data.error;
  if (data && Array.isArray(data.detail)) return data.detail.map((d) => `${(d.loc || []).join('.')}: ${d.msg}`).join('; ');
  if (data && data.detail) return String(data.detail);
  return `HTTP ${status}`;
}

async function api(path, { method = 'GET', body } = {}) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let r;
  try {
    r = await fetch(path, opts);
  } catch (err) {
    throw new ApiError(0, `Cannot reach Jig (${method} ${path}): ${err.message}`);
  }
  if (r.status === 401) {
    signedOut('Your session has ended. Please sign in again.');
    throw new ApiError(401, 'Signed out');
  }
  if (r.status === 204) return null;
  const text = await r.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new ApiError(r.status, `${method} ${path}: the server sent a response that is not JSON (HTTP ${r.status})`);
    }
  }
  if (!r.ok) throw new ApiError(r.status, `${method} ${path}: ${errorText(data, r.status)}`);
  return data;
}

/** Run an action from a control, disabling it meanwhile and surfacing any error. */
async function act(control, fn) {
  if (control) control.disabled = true;
  try {
    return await fn();
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 401)) showError(err.message);
    return undefined;
  } finally {
    if (control) control.disabled = false;
  }
}

/* ---------- preferences saved on this device ---------- */

const PREF = { readOnly: 'jig.readOnly', showWorking: 'jig.showWorking' };

function readFlag(key) {
  let v;
  try {
    v = localStorage.getItem(key);
  } catch (err) {
    showError(`Jig couldn\u2019t read the saved setting ${key}: ${err.message}. It is off until you set it again in Settings.`);
    return false;
  }
  if (v === null || v === '0') return false;
  if (v === '1') return true;
  showError(`The saved setting ${key} has an unexpected value ${q(v)}. It is off until you set it again in Settings.`);
  return false;
}

const prefs = { readOnly: readFlag(PREF.readOnly), showWorking: readFlag(PREF.showWorking) };

function applyPrefs() {
  document.documentElement.classList.toggle('show-working', prefs.showWorking);
  $('readonly-note').hidden = !prefs.readOnly;
  $('read-only').checked = prefs.readOnly;
  $('show-working').checked = prefs.showWorking;
}

for (const [id, name] of [['read-only', 'readOnly'], ['show-working', 'showWorking']]) {
  $(id).addEventListener('change', (e) => {
    try {
      localStorage.setItem(PREF[name], e.target.checked ? '1' : '0');
      prefs[name] = e.target.checked;
    } catch (err) {
      showError(`Jig couldn\u2019t save that setting on this device: ${err.message}`);
    }
    applyPrefs();
  });
}
applyPrefs();

/* ---------- appearance (theme.js resolves light or dark before first paint) ---------- */

const avatar = $('avatar');

function syncAppearance() {
  avatar.theme = document.documentElement.dataset.theme;
  for (const r of document.querySelectorAll('input[name="appearance"]')) r.checked = r.value === window.jigAppearance.choice;
}
document.addEventListener('jig-themechange', syncAppearance);
for (const r of document.querySelectorAll('input[name="appearance"]')) {
  r.addEventListener('change', () => {
    try {
      window.jigAppearance.set(r.value);
    } catch (err) {
      showError(`Jig couldn\u2019t change the appearance: ${err.message}`);
      syncAppearance();
    }
  });
}
syncAppearance();
if (window.jigAppearance.problem) showError(window.jigAppearance.problem);

/* ---------- sign-in ---------- */

let started = false;
// What GET /auth/session said: source is "local" (this computer) or "tailnet" (another device, via Tailscale).
let session = { authenticated: false, source: 'local', device: null };
let pendingPairCode = ''; // from a #pair= link, until it is used

async function boot() {
  let loginError = '';
  const m = location.hash.match(/^#code=([A-Za-z0-9_-]+)$/);
  const p = location.hash.match(/^#pair=([A-Za-z0-9-]+)$/);
  if (m || p) history.replaceState(null, '', location.pathname + location.search); // drop the one-time code at once
  if (p) pendingPairCode = p[1];
  if (m) {
    try {
      await api('/auth/session', { method: 'POST', body: { code: m[1] } });
    } catch (err) {
      loginError = `That sign-in link did not work: ${err.message.replace(/^POST \/auth\/session: /, '')}`;
    }
  }
  try {
    session = await api('/auth/session');
  } catch (err) {
    showError(err.message);
    setConn('Unreachable', 'bad');
    return;
  }
  if (session.authenticated) {
    if (pendingPairCode) {
      pendingPairCode = '';
      showError('This browser is already signed in to Jig, so the pairing code was not used. It expires by itself within 5 minutes.');
    }
    start();
  } else {
    signedOut(loginError || session.reason || '');
  }
}

function signedOut(message = '') {
  started = false;
  session = { ...session, authenticated: false, device: null };
  stopEvents();
  stopPairing();
  $('app').hidden = true;
  $('logout').hidden = true;
  $('agent-toggle').disabled = true;
  health.model = null;
  setConn('Signed out', 'bad');
  const pairing = session.source === 'tailnet' || Boolean(pendingPairCode);
  $('login-form').hidden = pairing;
  $('pair-form').hidden = !pairing;
  $('pair-use-token').hidden = session.source === 'tailnet';
  $('login').setAttribute('aria-labelledby', pairing ? 'pair-heading' : 'login-heading');
  const dialog = $('login');
  if (!dialog.open) dialog.showModal();
  if (pairing) {
    $('pair-error').textContent = message;
    if (pendingPairCode) $('pair-code').value = displayPairCode(pendingPairCode);
    if (!$('pair-name').value) $('pair-name').value = guessDeviceName();
    ($('pair-code').value ? $('pair-name') : $('pair-code')).focus();
  } else {
    $('login-error').textContent = message;
    $('login-token').focus();
  }
}

const displayPairCode = (c) => {
  const s = c.toUpperCase().replace(/[^A-Z0-9]/g, '');
  return s.length === 8 ? `${s.slice(0, 4)}-${s.slice(4)}` : s;
};

/** A starting suggestion for the device's name; the person pairing it can change it. */
function guessDeviceName() {
  const ua = navigator.userAgent;
  const kinds = [[/iPhone/, 'iPhone'], [/iPad/, 'iPad'], [/Android.*Mobile/, 'Android phone'], [/Android/, 'Android tablet'],
    [/CrOS/, 'Chromebook'], [/Macintosh/, 'Mac'], [/Windows/, 'Windows PC'], [/Linux/, 'Linux computer']];
  const hit = kinds.find(([re]) => re.test(ua));
  return hit ? hit[1] : '';
}

$('pair-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const button = e.submitter;
  button.disabled = true;
  $('pair-error').textContent = '';
  try {
    // Not api(): a wrong code is a 401 here, which must not look like "your session has ended".
    let r;
    try {
      r = await fetch('/auth/pair', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: $('pair-code').value.trim(), name: $('pair-name').value.trim() }),
      });
    } catch (err) {
      throw new Error(`Cannot reach Jig: ${err.message}`);
    }
    const data = await r.json().catch(() => null);
    if (!r.ok) throw new Error(errorText(data, r.status));
    pendingPairCode = '';
    $('pair-code').value = '';
    session = await api('/auth/session');
    if (!session.authenticated) {
      throw new Error('Jig paired this device, but this browser did not keep the sign-in cookie. Check that cookies are allowed for this site, then pair again with a new code.');
    }
    $('login').close();
    start();
  } catch (err) {
    $('pair-error').textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

$('pair-use-token').addEventListener('click', () => {
  pendingPairCode = '';
  signedOut('');
});

$('login').addEventListener('cancel', (e) => e.preventDefault());
$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = $('login-token');
  const button = e.submitter;
  button.disabled = true;
  $('login-error').textContent = '';
  try {
    await api('/auth/session', { method: 'POST', body: { token: input.value } });
    input.value = '';
    $('login').close();
    start();
  } catch (err) {
    $('login-error').textContent = err.message.replace(/^POST \/auth\/session: /, '');
  } finally {
    button.disabled = false;
  }
});

$('logout').addEventListener('click', async () => {
  if (session.device && !(await askConfirm({
    title: 'Sign out and unpair this device?',
    body: [`Signing out removes ${q(session.device.name)} from Jig\u2019s paired devices.`,
      'To use Jig here again, pair it again from Settings on the computer Jig runs on.'],
    ok: 'Sign out',
  }))) return;
  await act($('logout'), async () => {
    await api('/auth/logout', { method: 'POST' });
    signedOut('Signed out.');
  });
});

/* ---------- a friendly confirmation, for anything that turns something on or off ---------- */

function askConfirm({ title, body, ok, danger = false }) {
  const dialog = $('confirm');
  $('confirm-title').textContent = title;
  $('confirm-body').replaceChildren(...body.map((b) => (b instanceof Node ? b : el('p', { text: b }))));
  $('confirm-ok').textContent = ok;
  $('confirm-ok').className = `btn ${danger ? 'btn-danger' : 'btn-primary'}`;
  dialog.returnValue = '';
  dialog.showModal();
  $('confirm-cancel').focus();
  return new Promise((resolve) => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'ok'), { once: true });
  });
}

const bullets = (lines) => el('ul', { class: 'confirm-list' }, lines.filter(Boolean).map((t) => el('li', { text: t })));

function start() {
  if (started) return;
  started = true;
  $('app').hidden = false;
  $('logout').hidden = false;
  route(false);
  connectEvents();
  refreshAll();
}

function refreshAll() {
  loadStatus();
  loadActivity();
  loadApprovals();
  loadMemory();
  loadRules();
  if (currentSection() === 'history') loadAudit(true);
}

/* ---------- main screen and Settings (hash routes) ---------- */

const SECTIONS = ['model', 'rules', 'memory', 'history', 'startup', 'devices', 'power', 'appearance', 'chat'];

function currentSection() {
  const m = location.hash.match(/^#settings(?:\/([a-z]+))?$/);
  return m ? (m[1] || 'model') : null;
}

function route(moveFocus = true) {
  const section = currentSection();
  if (section && !SECTIONS.includes(section)) {
    showError(`There\u2019s no Settings section called ${q(section)}.`);
    history.replaceState(null, '', '#settings');
    route(moveFocus);
    return;
  }
  $('settings').hidden = !section;
  $('main').hidden = Boolean(section);
  if (section !== 'devices') stopPairing();
  if (section) {
    for (const s of document.querySelectorAll('.set-section')) s.hidden = s.dataset.section !== section;
    for (const a of document.querySelectorAll('.settings-nav a')) {
      if (a.dataset.section === section) {
        a.setAttribute('aria-current', 'page');
        a.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      } else {
        a.removeAttribute('aria-current');
      }
    }
    if (section === 'history') loadAudit(true);
    if (section === 'devices') loadRemote();
    if (section === 'power') loadPower();
    if (moveFocus) {
      const heading = document.querySelector(`.set-section[data-section="${section}"] h3`);
      heading.tabIndex = -1;
      heading.focus();
    }
  } else if (moveFocus && location.hash !== '#chat-input') {
    $('open-settings').focus();
  }
}
window.addEventListener('hashchange', () => route(true));

/* ---------- health: one quiet dot, and a banner that explains any problem ---------- */

const health = { conn: { text: 'Not connected', tone: 'muted' }, model: null };
let offState = null; // set once Jig is turning off: the page shows "Jig is off" instead of connection problems
let ownStop = false; // this page asked Jig to turn off (rather than another device or the command line)

function setConn(text, tone) {
  health.conn = { text, tone };
  $('st-conn').textContent = text;
  renderHealth();
}

function connProblem() {
  const { text, tone } = health.conn;
  if (tone !== 'bad' || text === 'Signed out') return null;
  if (text === 'Unreachable') {
    return { title: 'Jig isn\u2019t answering.', fix: 'Check that Jig is running on this computer (jig serve, or start it from the Start menu), then choose Check again.', detail: '' };
  }
  return { title: 'Jig has lost its live connection.', fix: 'It is trying again by itself. If this keeps happening, check that Jig is still running, then choose Check again.', detail: text };
}

function renderHealth() {
  if (offState) {
    $('health-banner').hidden = true;
    $('conn').dataset.tone = 'muted';
    $('conn').title = 'Jig is off';
    $('conn-text').textContent = 'Jig is off';
    return;
  }
  const problem = health.model || connProblem();
  const dot = $('conn');
  dot.dataset.tone = problem ? 'bad' : health.conn.tone;
  dot.title = problem ? problem.title : `Jig: ${health.conn.text}`;
  $('conn-text').textContent = problem ? problem.title : health.conn.text;
  const banner = $('health-banner');
  banner.hidden = !problem;
  if (problem) {
    $('health-title').textContent = problem.title;
    $('health-fix').textContent = problem.fix;
    $('health-detail').textContent = problem.detail;
    $('health-detail').hidden = !problem.detail;
  }
}

$('health-retry').addEventListener('click', () => act($('health-retry'), async () => {
  if (wsWanted && !ws) connectEvents();
  await loadStatus();
}));

/* ---------- live events and the avatar ---------- */

let ws = null;
let wsRetry = 0;
let wsTimer = 0;
let wsWanted = false;

function connectEvents() {
  wsWanted = true;
  clearTimeout(wsTimer);
  setConn('Connecting\u2026', 'warn');
  const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/events`);
  ws = socket;
  socket.addEventListener('open', () => {
    if (wsRetry > 0) refreshAll(); // catch up on anything missed while disconnected
    wsRetry = 0;
    setConn('Live', 'ok');
  });
  socket.addEventListener('message', (e) => {
    let event;
    try {
      event = JSON.parse(e.data);
    } catch {
      showError('The event stream sent a message that is not JSON.');
      return;
    }
    handleEvent(event);
  });
  socket.addEventListener('close', async (e) => {
    if (ws !== socket || !wsWanted) return;
    ws = null;
    if (e.code === 1013) showError(`Event stream: ${e.reason || 'the connection fell behind and was closed'}`);
    // A refused handshake reaches the browser as 1006, so ask the server whether the session is still valid.
    try {
      session = await api('/auth/session');
      if (!session.authenticated) {
        signedOut('Your session has ended. Please sign in again.');
        return;
      }
    } catch {
      /* the server is unreachable; keep retrying below */
    }
    wsRetry += 1;
    const delay = Math.min(15000, 1000 * 2 ** Math.min(wsRetry - 1, 4));
    setConn(`Disconnected, retrying in ${Math.round(delay / 1000)} s`, 'bad');
    wsTimer = setTimeout(connectEvents, delay);
  });
}

function stopEvents() {
  wsWanted = false;
  clearTimeout(wsTimer);
  if (ws) {
    const socket = ws;
    ws = null;
    socket.close();
  }
}

function applyAvatar(d) {
  const background = Boolean(d.background);
  const opts = d.state === 'working' ? { task: d.variant, background } : { background };
  try {
    avatar.setState(d.state, opts);
  } catch (err) {
    showError(`The avatar could not show state ${q(d.state)}: ${err.message}`);
    return;
  }
  $('avatar-says').textContent = avatarWords(d.state, d.variant, background);
  document.documentElement.dataset.jigState = d.state;
}

const WORKING_WORDS = {
  browsing: 'Reading\u2026', writing: 'Writing\u2026', coding: 'Running some code\u2026',
  shopping: 'Working on a purchase\u2026', scheduling: 'Checking the time\u2026',
};
const STATE_WORDS = {
  idle: 'Ready when you are.', monitoring: 'Keeping an eye on things.', thinking: 'Thinking\u2026', talking: 'Talking\u2026',
  approval: 'Waiting for your answer.', success: 'All done!', error: 'Something went wrong. The details are on screen.',
  paused: 'Paused.',
};

/** The avatar's one short caption, also announced to screen readers. */
function avatarWords(state, variant, background) {
  const words = state === 'working' ? WORKING_WORDS[variant] : STATE_WORDS[state];
  if (!words) return `Jig is ${state}${variant ? `: ${variant}` : ''}.`;
  return background ? `${words.replace(/(\u2026|\.)$/, '')} quietly in the background\u2026` : words;
}

const refreshers = { activity: loadActivity, approvals: loadApprovals, memory: loadMemory, rules: loadRules,
  status: loadStatus, audit: () => loadAudit(true) };
const pendingRefresh = {};
function refreshSoon(area, delay = 300) {
  clearTimeout(pendingRefresh[area]);
  pendingRefresh[area] = setTimeout(() => refreshers[area](), delay);
}

function handleEvent(event) {
  const t = event.type;
  const d = event.data || {};
  if (t === 'avatar.state') {
    applyAvatar(d);
    return;
  }
  if (t === 'tool.start' && d.task_id) {
    currentTool.set(d.task_id, d.tool);
    renderDoing();
  }
  if (t === 'run.end' && d.task_id) currentTool.delete(d.task_id);
  if (t === 'task.status' || t === 'goal.status' || t === 'run.start' || t === 'run.end') refreshSoon('activity');
  if (t === 'approval.requested' || t === 'approval.resolved') {
    refreshSoon('approvals');
    refreshSoon('activity');
  }
  if (t === 'memory.changed') refreshSoon('memory');
  if (t === 'rule.changed') refreshSoon('rules');
  if (t === 'agent.status') {
    setAgentPaused(d.paused);
    refreshSoon('status');
  }
  if (t === 'power.stopping') {
    showOff({ scope: d.scope, fromElsewhere: !ownStop });
    return;
  }
  if (currentSection() === 'history' && !auditPaged) refreshSoon('audit', 1000);
}

if (!STATES.includes('paused')) showError('This avatar component does not support the paused state.');

/* ---------- model and connection ---------- */

const VAULT_PLACES = {
  dpapi: 'Windows\u2019 protected store, locked to your account',
  keyring: 'your system keychain',
  keyfile: 'Jig\u2019s database, encrypted with your key file',
};

function capsText(c) {
  if (!c) return 'not run';
  const parts = [];
  if ('tool_calling' in c) parts.push(`tool calling ${c.tool_calling ? 'passed' : 'FAILED'}`);
  if ('structured_output' in c) parts.push(`structured output ${c.structured_output ? 'passed' : 'FAILED'}`);
  if ('vision' in c) parts.push(`vision ${c.vision ? 'passed' : 'FAILED'}`);
  if (c.same_as_agent) parts.push('same model as the agent');
  return parts.join(', ') || 'not run';
}

async function loadStatus() {
  let s;
  try {
    s = await api('/status');
  } catch (err) {
    if (err.status === 401) return;
    $('st-model').textContent = 'unavailable';
    health.model = err.status === 0
      ? { title: 'Jig isn\u2019t answering.', fix: 'Check that Jig is running on this computer, then choose Check again.', detail: err.message }
      : { title: 'Jig can\u2019t reach its model.', fix: 'Check that your local model server is running and reachable at the address in Settings, Model and connection, then choose Check again.', detail: err.message };
    renderHealth();
    return;
  }
  $('st-endpoint').textContent = s.model_endpoint;
  $('st-model').textContent = s.model.model;
  $('st-context').textContent = s.model.context_tokens ? `${s.model.context_tokens.toLocaleString('en-GB')} tokens` : 'not reported';
  $('st-caps').textContent = capsText(s.capabilities.agent);
  $('st-sentinel').textContent = `${s.sentinel_model.model}: ${capsText(s.capabilities.sentinel)}`;
  $('st-vault').textContent = VAULT_PLACES[s.vault_backend] || `an unrecognised store (${s.vault_backend})`;
  setAgentPaused(s.agent.paused);
  const caps = s.capabilities.agent;
  health.model = caps && caps.tool_calling === false
    ? { title: 'Jig\u2019s model can\u2019t use tools.', fix: 'Jig needs a model that passes the tool-calling check. Load one that does in your model server, then restart Jig.', detail: `${s.model.model}: ${capsText(caps)}` }
    : null;
  renderHealth();
}
$('status-refresh').addEventListener('click', () => act($('status-refresh'), loadStatus));

/* ---------- what Jig's up to ---------- */

let agentPaused = false;
let tasksCache = [];
let goalsCache = [];
const currentTool = new Map(); // task id -> the tool it is using now (from live tool.start events)

const TOOL_DOING = {
  web_fetch: 'reading a web page', read_file: 'reading a file', list_files: 'looking through files', write_file: 'writing a file',
  note_write: 'writing a note', note_list: 'looking at its notes', memory_search: 'checking what it remembers',
  memory_add: 'remembering something', memory_forget: 'forgetting something', current_time: 'checking the time',
  browser_open: 'opening a web page', browser_read: 'reading a web page', browser_screenshot: 'looking at a web page',
  browser_click: 'using a web page', browser_type: 'typing into a web page', browser_fill: 'filling in a form',
  browser_submit: 'submitting a form', browser_login: 'signing in to a website', run_command: 'running a command',
  run_python: 'running some code',
};

function setAgentPaused(paused) {
  agentPaused = paused;
  const b = $('agent-toggle');
  b.disabled = false;
  b.textContent = paused ? 'Resume Jig' : 'Pause Jig';
  b.setAttribute('aria-pressed', String(paused));
  b.classList.toggle('btn-warn', paused);
  $('st-agent').textContent = paused ? 'Paused: no schedules fire and no tasks start' : 'Running';
  renderDoing();
}

$('agent-toggle').addEventListener('click', () => act($('agent-toggle'), async () => {
  const status = await api(agentPaused ? '/agent/resume' : '/agent/pause', { method: 'POST' });
  setAgentPaused(status.paused);
}));

function taskSentence(t) {
  if (t.status === 'waiting_approval') return 'Waiting for your answer';
  if (t.status === 'running') {
    const doing = TOOL_DOING[currentTool.get(t.id)];
    return doing ? `${doing[0].toUpperCase()}${doing.slice(1)}\u2026` : 'Working on it\u2026';
  }
  if (t.status === 'queued') return t.not_before && new Date(t.not_before) > new Date() ? `Starts at ${clock(t.not_before)}` : 'Lined up to start';
  if (t.status === 'paused') return 'Paused';
  if (t.status === 'done') return `Finished at ${clock(t.finished_at)}`;
  if (t.status === 'cancelled') return 'Stopped';
  if (t.status === 'failed' || t.status === 'blocked') return `Couldn\u2019t finish: ${t.error || t.status}`;
  return t.status;
}

const isActive = (t) => t.status === 'running' || t.status === 'waiting_approval';

function renderDoing() {
  const live = tasksCache.filter((t) => !TERMINAL_TASK.has(t.status));
  const active = live.filter(isActive);
  const queued = live.filter((t) => t.status === 'queued');
  const planning = goalsCache.filter((g) => g.status === 'planning');
  let text;
  if (agentPaused) {
    const n = active.length + queued.length;
    text = n ? `Jig is paused. ${plural(n, 'job')} will carry on when you resume.` : 'Jig is paused. Nothing new starts until you resume.';
  } else if (active.length) {
    const t = active.find((x) => x.status === 'running') || active[0];
    const more = active.length + queued.length - 1;
    const doing = t.status === 'waiting_approval' ? 'waiting for your answer' : (TOOL_DOING[currentTool.get(t.id)] || 'working on it');
    text = `${q(t.title)}: ${doing}${t.status === 'running' ? '\u2026' : '.'}${more > 0 ? ` And ${plural(more, 'more job')}.` : ''}`;
  } else if (planning.length) {
    text = `Planning ${q(planning[0].title)}\u2026`;
  } else if (queued.length) {
    text = queued.length === 1 ? `${q(queued[0].title)} is lined up to start.` : `${plural(queued.length, 'job')} lined up to start.`;
  } else if (live.length) {
    text = live.length === 1 ? `${q(live[0].title)} is paused.` : `${plural(live.length, 'job')} paused.`;
  } else {
    text = 'Nothing on the go in the background.';
  }
  $('doing-text').textContent = text;
  const busy = active.length + queued.length + planning.length > 0;
  $('doing').dataset.busy = String(busy);
  $('doing-stop').hidden = !busy && !live.length;
  renderDoingList(live, planning);
}

function stoppable() {
  const live = tasksCache.filter((t) => !TERMINAL_TASK.has(t.status));
  const goals = new Map(goalsCache.filter((g) => !TERMINAL_GOAL.has(g.status) && (g.status === 'planning' || live.some((t) => t.goal_id === g.id)))
    .map((g) => [g.id, g]));
  return { goals: [...goals.values()], tasks: live.filter((t) => !t.goal_id) };
}

$('doing-stop').addEventListener('click', () => act($('doing-stop'), async () => {
  const { goals, tasks } = stoppable();
  const names = [...goals.map((g) => g.title), ...tasks.map((t) => t.title)];
  if (!names.length) return;
  if (!confirm(`Stop everything Jig is doing in the background?\n\n${names.map((n) => `\u2022 ${n}`).join('\n')}\n\nAnything already finished is kept.`)) return;
  for (const g of goals) await api(`/goals/${g.id}/cancel`, { method: 'POST' });
  for (const t of tasks) await api(`/tasks/${t.id}/cancel`, { method: 'POST' });
  await loadActivity();
}));

function doingItem(t) {
  const label = `task ${t.title}`;
  const actions = el('div', { class: 'item-actions' });
  if (t.status === 'waiting_approval') {
    actions.append(el('button', { type: 'button', class: 'btn btn-small btn-warn', text: 'Answer', 'aria-label': `Answer the question for ${label}`,
      onclick: () => { $('activity').close(); showFirstPending(); } }));
  }
  if (['queued', 'running', 'waiting_approval'].includes(t.status)) {
    actions.append(el('button', { type: 'button', class: 'btn btn-small', text: 'Pause', 'aria-label': `Pause ${label}`,
      onclick: (e) => act(e.currentTarget, async () => { await api(`/tasks/${t.id}/pause`, { method: 'POST' }); await loadActivity(); }) }));
  }
  if (t.status === 'paused') {
    actions.append(el('button', { type: 'button', class: 'btn btn-small btn-approve', text: 'Resume', 'aria-label': `Resume ${label}`,
      onclick: (e) => act(e.currentTarget, async () => { await api(`/tasks/${t.id}/resume`, { method: 'POST' }); await loadActivity(); }) }));
  }
  if (!TERMINAL_TASK.has(t.status)) {
    actions.append(el('button', { type: 'button', class: 'btn btn-small btn-danger', text: 'Stop', 'aria-label': `Stop ${label}`,
      onclick: (e) => act(e.currentTarget, async () => {
        if (!confirm(`Stop ${q(t.title)}? Anything already finished is kept.`)) return;
        await api(`/tasks/${t.id}/cancel`, { method: 'POST' });
        await loadActivity();
      }) }));
  }
  const bad = t.status === 'failed' || t.status === 'blocked';
  return el('div', { class: `doing-item${bad ? ' is-bad' : ''}`, 'data-testid': 'doing-item', dataset: { id: t.id, status: t.status } },
    el('div', { class: 'doing-item-text' }, el('p', { class: 'doing-title', text: t.title }), el('p', { class: bad ? 'error-text' : 'hint-quiet', text: taskSentence(t) })),
    actions);
}

function renderDoingList(live, planning) {
  const list = $('doing-list');
  const recent = tasksCache.filter((t) => TERMINAL_TASK.has(t.status) && t.finished_at
    && Date.now() - new Date(t.finished_at).getTime() < 24 * 3600 * 1000).slice(0, 3);
  const items = [
    ...planning.map((g) => el('div', { class: 'doing-item', 'data-testid': 'doing-item', dataset: { id: g.id, status: g.status } },
      el('div', { class: 'doing-item-text' }, el('p', { class: 'doing-title', text: g.title }), el('p', { class: 'hint-quiet', text: 'Planning the steps\u2026' })))),
    ...live.map(doingItem),
  ];
  list.replaceChildren(...items);
  if (!items.length) list.append(empty('Nothing on the go in the background.', 'Give Jig a longer job below and it will work through it while you get on with other things.'));
  if (recent.length) list.append(el('h3', { class: 'doing-recent', text: 'Recently' }), ...recent.map(doingItem));
}

$('doing-open').addEventListener('click', () => $('activity').showModal());
$('activity-close').addEventListener('click', () => $('activity').close());
$('activity').addEventListener('click', (e) => { if (e.target === $('activity')) $('activity').close(); });

/* ---------- chat ---------- */

let sessionId = null;
let chatBusy = false;
const liveChatRuns = new Set(); // runs whose reply is streaming here; their approvals appear inside the reply
const chatLog = $('chat-log');
const chatInput = $('chat-input');

function nearBottom() {
  return chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 80;
}

function welcome() {
  return el('div', { class: 'welcome', 'data-testid': 'chat-welcome' },
    el('p', { class: 'welcome-title', text: 'Hello! What shall we do?' }),
    el('p', { class: 'hint', text: 'Ask me anything, or give me something to do. I\u2019ll always check with you before I change, send or spend anything.' }));
}

function addMessage(role, text = '') {
  chatLog.querySelector('.welcome')?.remove();
  const content = el('div', { class: 'content', text });
  const msg = el('article', { class: `msg ${role}`, 'aria-label': role === 'user' ? 'You said' : 'Jig replied',
    'data-testid': role === 'user' ? 'chat-user' : 'chat-jig' }, content);
  chatLog.append(msg);
  chatLog.scrollTop = chatLog.scrollHeight;
  return { msg, content };
}

const VERDICT_WORDS = { allow: 'looks fine', ask_user: 'ask you first', deny: 'do not do this' };

function describeWorking(ev) {
  const d = ev.data;
  switch (ev.type) {
    case 'tool.start': return `Using ${d.tool}`;
    case 'sentinel.verdict': return `Sentinel checked ${d.tool}: ${VERDICT_WORDS[d.verdict] || d.verdict}, ${d.risk} risk. ${d.reason}`;
    case 'approval.resolved': return `You ${d.status === 'approved' ? 'said yes to' : d.status === 'denied' ? 'said no to' : `answered (${d.status})`} ${d.tool}`;
    default: return null;
  }
}

async function sendChat(message) {
  chatBusy = true;
  $('chat-send').disabled = true;
  addMessage('user', message);
  const { msg, content } = addMessage('jig');
  content.classList.add('typing');
  let thinking = null;
  let working = null;
  let finished = false;
  let runId = null;

  const fail = (text) => {
    msg.classList.add('error');
    msg.append(el('p', { class: 'error-text', text: `Jig couldn\u2019t finish this reply: ${text}` }));
    showError(`Chat: ${text}`);
  };
  const note = (text) => {
    if (!working) {
      working = el('ul', { class: 'working', 'aria-label': 'What Jig did', 'data-testid': 'chat-working' });
      msg.append(working);
    }
    working.append(el('li', { text }));
  };

  const handle = (item) => {
    const follow = nearBottom();
    if (item.type === 'start') {
      sessionId = item.session_id;
      runId = item.run_id;
      liveChatRuns.add(runId);
    } else if (item.type === 'reasoning') {
      if (!thinking) {
        const pre = el('pre');
        thinking = { pre, box: el('details', { class: 'thinking', 'data-testid': 'chat-thinking' }, el('summary', { text: 'Thinking' }), pre) };
        msg.insertBefore(thinking.box, content);
      }
      thinking.pre.textContent += item.text;
    } else if (item.type === 'content') {
      content.textContent += item.text;
    } else if (item.type === 'event') {
      const ev = item.event;
      if (ev.type === 'approval.requested') {
        if (!ev.data.resumed) placeApproval(ev.data.approval_id, msg);
        content.classList.remove('typing');
      } else if (ev.type === 'approval.resolved') {
        refreshCard(ev.data.approval_id);
        content.classList.add('typing');
        const text = describeWorking(ev);
        if (text) note(text);
      } else if (ev.type === 'tool.end') {
        if (!ev.data.ok) {
          msg.append(el('p', { class: 'reply-problem', 'data-testid': 'chat-problem',
            text: `Something went wrong while ${TOOL_DOING[ev.data.tool] || `using ${ev.data.tool}`} (${ev.data.tool}), so Jig carried on without it.` }));
        }
      } else {
        const text = describeWorking(ev);
        if (text) note(text);
      }
    } else if (item.type === 'done') {
      finished = true;
      sessionId = item.session_id;
      if (!content.textContent) content.textContent = item.final;
    } else if (item.type === 'error') {
      finished = true;
      sessionId = item.session_id;
      fail(item.error);
    } else {
      fail(`unexpected stream item ${q(item.type)}`);
    }
    if (follow) chatLog.scrollTop = chatLog.scrollHeight;
  };

  try {
    let r;
    try {
      r = await fetch('/chat', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, session_id: sessionId, mode: prefs.readOnly ? 'research' : 'action' }),
      });
    } catch (err) {
      fail(`cannot reach Jig: ${err.message}`);
      return;
    }
    if (r.status === 401) {
      signedOut('Your session has ended. Please sign in again.');
      fail('signed out');
      return;
    }
    if (!r.ok) {
      const text = await r.text();
      let data = null;
      try { data = JSON.parse(text); } catch { /* not JSON */ }
      fail(data ? errorText(data, r.status) : `HTTP ${r.status}`);
      return;
    }
    const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 1);
        if (line.trim()) handle(JSON.parse(line));
      }
    }
    if (buf.trim()) handle(JSON.parse(buf));
    if (!finished) fail('the reply stream ended before Jig finished');
  } catch (err) {
    fail(err.message);
  } finally {
    content.classList.remove('typing');
    if (runId) liveChatRuns.delete(runId);
    chatBusy = false;
    $('chat-send').disabled = false;
    loadApprovals();
  }
}

$('chat-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message || chatBusy) return;
  chatInput.value = '';
  fitInput();
  sendChat(message);
});
function fitInput() {
  chatInput.style.height = 'auto';
  chatInput.style.height = `${Math.min(chatInput.scrollHeight + 2, 200)}px`;
}
chatInput.addEventListener('input', fitInput);
chatInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $('chat-form').requestSubmit();
  }
});
$('chat-new').addEventListener('click', () => {
  sessionId = null;
  const keep = [...chatLog.querySelectorAll('.notice')].filter((n) => n.querySelector('[data-status="pending"]'));
  chatLog.replaceChildren(welcome(), ...keep);
  chatInput.focus();
});
chatLog.append(welcome());

/* ---------- approvals, inline in the conversation ---------- */

const base = (p) => String(p ?? '').split(/[\\/]/).pop();
const host = (u) => {
  try { return new URL(u).host; } catch { return String(u); }
};
const QUESTION = {
  write_file: (x) => `Can I save this to ${base(x.path)}?`,
  note_write: (x) => `Can I save a note called ${q(x.title)}?`,
  memory_add: () => 'Can I remember this about you?',
  memory_forget: (x) => `Can I forget memory #${x.memory_id}?`,
  web_fetch: (x) => `Can I open a page on ${host(x.url)}?`,
  browser_open: (x) => `Can I open ${host(x.url)} in my browser?`,
  browser_click: () => 'Can I click something on this web page?',
  browser_type: () => 'Can I type into this web page?',
  browser_fill: () => 'Can I fill in a box on this web page?',
  browser_submit: () => 'Can I submit this form?',
  browser_login: (x) => `Can I sign in as ${q(x.username)}?`,
  run_command: () => 'Can I run this command?',
  run_python: () => 'Can I run this Python code?',
};
const WILL = {
  write_file: (x) => `Jig will write ${String(x.content ?? '').length.toLocaleString('en-GB')} characters to the file ${q(x.path)} in its `
    + `workspace${x.overwrite ? ', replacing that file if it already exists.' : '. If that file already exists, nothing is replaced and the step fails.'}`,
  memory_forget: (x) => `Jig will permanently delete memory #${x.memory_id}.`,
  memory_add: (x) => `Jig will remember: ${q(x.content)}.`,
  note_write: (x) => `Jig will save a private note titled ${q(x.title)}.`,
  web_fetch: (x) => `Jig will fetch ${x.url}${x.headers && Object.keys(x.headers).length ? ' with extra request headers (see Why am I asking?)' : ''}.`,
  browser_open: (x) => `Jig will open ${x.url} in its sandboxed browser.`,
  browser_click: (x) => `Jig will click the element ${q(x.selector)} on the current page.`,
  browser_type: (x) => `Jig will type ${q(x.text)} into ${q(x.selector)}${x.press_enter ? ' and then press Enter' : ''}.`,
  browser_fill: (x) => `Jig will fill ${q(x.selector)} with ${q(x.value)}.`,
  browser_submit: (x) => `Jig will submit the form ${q(x.selector)} on the current page.`,
  browser_login: (x) => `Jig will sign in on the current page as ${q(x.username)}.`,
  run_command: () => 'Jig will run this command in its sandbox:',
  run_python: () => 'Jig will run this Python code in its sandbox:',
};
/* The exact thing that will be written, sent or run, shown on the card itself. */
const PREVIEW = { write_file: 'content', note_write: 'body', run_command: 'command', run_python: 'code' };
const VERDICT_LABEL = { allow: 'Looks fine', ask_user: 'Ask you first', deny: 'Do not do this' };
const RISK_MARK = { low: '\u25CF', medium: '\u25B2', high: '\u25A0' };

function argValue(v) {
  if (typeof v === 'boolean') return el('span', { text: v ? 'yes' : 'no' });
  if (v === null || v === undefined) return el('span', { class: 'muted-inline', text: 'not set' });
  if (typeof v === 'object') return el('pre', { class: 'args', text: json(v) });
  const s = String(v);
  return s.length > 80 || s.includes('\n') ? el('pre', { class: 'args', text: s }) : el('code', { text: s });
}

const cards = new Map(); // approval id -> { node, where } for cards in the conversation

function approvalCard(a, where) {
  const s = a.sentinel;
  const pendingNow = a.status === 'pending';
  const risk = s ? s.risk : 'none';
  const idp = `ap-${a.id}-${where}`;
  const question = (QUESTION[a.tool] || (() => `Can I use ${a.tool}?`))(a.args);
  const willText = WILL[a.tool] ? WILL[a.tool](a.args) : `Jig will run ${a.tool} with exactly the details under Why am I asking?`;
  const argList = el('dl', { class: 'args-list' }, Object.entries(a.args).flatMap(([k, v]) => [el('dt', { text: k }), el('dd', {}, argValue(v))]));
  const previewKey = PREVIEW[a.tool];
  const preview = previewKey && a.args[previewKey] !== undefined ? String(a.args[previewKey]) : null;

  const verdict = s
    ? el('section', { class: 'verdict', dataset: { verdict: s.verdict, risk: s.risk }, 'aria-label': 'Sentinel\u2019s verdict', 'data-testid': 'sentinel-verdict' },
      el('h4', { text: 'Sentinel\u2019s verdict' }),
      el('div', { class: 'verdict-line' },
        el('span', { class: 'verdict-label', dataset: { verdict: s.verdict } }, VERDICT_LABEL[s.verdict] || s.verdict, el('span', { class: 'code', text: ` (${s.verdict})` })),
        el('span', { class: 'risk', dataset: { risk: s.risk }, 'data-testid': 'sentinel-risk' },
          el('span', { 'aria-hidden': 'true', text: `${RISK_MARK[s.risk] || '\u25C6'} ` }), `${s.risk[0].toUpperCase()}${s.risk.slice(1)} risk`)),
      el('p', { class: 'verdict-reason', text: s.reason }))
    : el('section', { class: 'verdict', dataset: { verdict: 'none' }, 'aria-label': 'Sentinel\u2019s verdict', 'data-testid': 'sentinel-verdict' },
      el('h4', { text: 'Sentinel\u2019s verdict' }),
      el('p', { class: 'verdict-reason', text: 'Not reviewed by the Sentinel: this action has no outbound or side effect.' }));

  const why = el('section', { class: 'why', 'aria-label': 'The rule that asked' },
    el('h4', { text: 'The rule that asked' }),
    el('ul', { class: 'reasons' }, a.reasons.map((r) => el('li', {}, el('code', { text: r.rule }), `: ${r.reason}`))));

  const exact = el('section', { class: 'will-happen', 'aria-label': 'Exactly what will happen', 'data-testid': 'approval-what' },
    el('h4', { text: pendingNow ? 'Exactly what will happen' : 'Exactly what Jig asked to do' }), argList);

  const disclosure = el('details', { class: 'why-ask', 'data-testid': 'approval-why' },
    el('summary', { text: pendingNow ? 'Why am I asking?' : 'Details' }),
    verdict, why, exact,
    el('details', { class: 'tech' }, el('summary', { text: 'Technical details' }),
      el('p', { class: 'meta', text: `Approval ${a.id} · task ${a.task_id || '—'} · run ${a.run_id || '—'} · tool ${a.tool} · asked ${when(a.created_at)}` }),
      el('pre', { class: 'args', text: json(a.args) })));

  const parts = [
    el('div', { class: 'ask-head' },
      el('span', { class: 'ask-mark', 'aria-hidden': 'true', text: risk === 'high' ? '!' : '?' }),
      el('h3', { id: `${idp}-title`, class: 'ask-title', text: question })),
  ];
  if (pendingNow) {
    if (risk === 'high') {
      parts.push(el('p', { class: 'risk-callout', 'data-testid': 'approval-high-risk' },
        el('strong', { text: 'High risk. ' }), s.reason));
    }
    parts.push(el('p', { class: 'will', 'data-testid': 'approval-will', text: `If you say yes: ${willText}` }));
    if (preview !== null) {
      const short = preview.length > 280 ? `${preview.slice(0, 280)}\u2026` : preview;
      parts.push(el('pre', { class: 'preview', 'data-testid': 'approval-preview', text: short }));
      if (preview.length > 280) parts.push(el('p', { class: 'hint-quiet', text: `Showing the first 280 of ${preview.length.toLocaleString('en-GB')} characters. All of it is under Why am I asking?` }));
    }
    const noteId = `${idp}-note`;
    const note = el('input', { id: noteId, type: 'text', placeholder: 'For example, why you said yes or no', 'data-testid': 'approval-note' });
    disclosure.append(el('label', { class: 'note-field', for: noteId }, 'Add a note (optional)', note));
    const answer = (approve) => (e) => act(e.currentTarget, async () => {
      await api(`/approvals/${a.id}`, { method: 'POST', body: { approve, note: note.value.trim() || null } });
      await refreshCard(a.id, true);
      loadApprovals();
    });
    parts.push(el('div', { class: 'decide' },
      el('button', { type: 'button', class: 'btn btn-yes', 'aria-label': `Yes, approve ${a.tool}`, 'data-testid': 'approval-approve', onclick: answer(true) }, 'Yes'),
      el('button', { type: 'button', class: 'btn btn-no', 'aria-label': `No, deny ${a.tool}`, 'data-testid': 'approval-deny', onclick: answer(false) }, 'No')));
  } else {
    const outcome = a.status === 'approved' ? 'You said yes.' : a.status === 'denied' ? 'You said no, so Jig didn\u2019t do it.' : `This question was ${a.status.replace(/_/g, ' ')}.`;
    parts.push(el('p', { class: 'outcome', 'data-testid': 'approval-outcome' }, outcome,
      a.note ? ` Your note: ${q(a.note)}.` : '', el('span', { class: 'hint-quiet', text: ` ${a.resolved_at ? `Answered ${when(a.resolved_at)}` : ''}` })));
  }
  parts.push(disclosure);
  return el('article', { class: `ask${pendingNow ? ' is-pending' : ' is-done'}`, 'aria-labelledby': `${idp}-title`, tabindex: '-1',
    'data-testid': 'approval', dataset: { id: a.id, status: a.status, risk, tool: a.tool } }, parts);
}

function swapCard(id, a, focus = false) {
  const entry = cards.get(id);
  if (!entry) return;
  const hadFocus = focus || entry.node.contains(document.activeElement);
  const fresh = approvalCard(a, entry.where);
  entry.node.replaceWith(fresh);
  entry.node = fresh;
  if (hadFocus) fresh.focus();
}

async function refreshCard(id, focus = false) {
  if (!cards.has(id)) return;
  try {
    swapCard(id, await api(`/approvals/${id}`), focus);
  } catch (err) {
    if (err.status !== 401) showError(err.message);
  }
}

/** Put a chat run's approval inside the reply that asked for it. */
async function placeApproval(id, msg) {
  if (cards.has(id)) return;
  cards.set(id, { node: el('div', { class: 'ask-loading', text: 'Jig has a question\u2026' }), where: 'chat' });
  msg.append(cards.get(id).node);
  try {
    swapCard(id, await api(`/approvals/${id}`));
  } catch (err) {
    if (err.status !== 401) showError(err.message);
  }
  chatLog.scrollTop = chatLog.scrollHeight;
}

/** A gentle notice in the conversation for a question raised by background work (or an earlier conversation). */
function addNotice(a) {
  const task = a.task_id ? tasksCache.find((t) => t.id === a.task_id) : null;
  const lead = a.task_id
    ? `While working on ${task ? q(task.title) : 'a background job'}, I have a question.`
    : 'From another conversation, I\u2019m still waiting for your answer.';
  const card = approvalCard(a, 'chat');
  const follow = nearBottom();
  chatLog.append(el('div', { class: 'notice', 'data-testid': 'background-approval', dataset: { id: a.id } },
    el('p', { class: 'notice-lead', text: lead }), card));
  cards.set(a.id, { node: card, where: 'chat' });
  if (follow) chatLog.scrollTop = chatLog.scrollHeight;
}

function showFirstPending() {
  for (const { node } of cards.values()) {
    if (node.dataset.status === 'pending' && node.isConnected) {
      if (currentSection()) location.hash = '';
      node.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      node.focus({ preventScroll: true });
      return;
    }
  }
}
$('approvals-badge').addEventListener('click', showFirstPending);

async function loadApprovals() {
  let pendingList, all;
  try {
    [pendingList, all] = await Promise.all([api('/approvals?status=pending'), api('/approvals')]);
  } catch (err) {
    if (err.status !== 401) showError(err.message);
    return;
  }
  if (!tasksCache.length && pendingList.some((a) => a.task_id)) await loadActivity();
  for (const a of [...pendingList].reverse()) {
    if (!cards.has(a.id) && !(a.run_id && liveChatRuns.has(a.run_id))) addNotice(a);
  }
  for (const [id, entry] of cards) {
    if (entry.node.dataset.status === 'pending' && !pendingList.some((p) => p.id === id)) {
      const fresh = all.find((x) => x.id === id);
      if (fresh) swapCard(id, fresh);
      else await refreshCard(id);
    }
  }
  const answered = all.filter((a) => a.status !== 'pending').slice(0, 20);
  $('approvals-done').replaceChildren(...answered.map((a) => approvalCard(a, 'history')));
  if (!answered.length) $('approvals-done').append(empty('You haven\u2019t answered any questions yet.'));
  const n = pendingList.length;
  const badge = $('approvals-badge');
  badge.hidden = n === 0;
  badge.textContent = n === 1 ? '1 question for you' : `${n} questions for you`;
  document.title = n ? `(${n}) Jig` : 'Jig';
}

/* ---------- activity detail (one tap deeper) ---------- */

function taskItem(t) {
  const outcome = t.error || t.result;
  return el('div', { class: 'item', 'data-testid': 'task', dataset: { id: t.id, status: t.status } },
    el('div', { class: 'item-head' }, statusBadge(t.status), el('span', { class: 'title', text: t.title }),
      el('span', { class: 'status', dataset: { s: 'mode' }, text: t.mode })),
    el('div', { class: 'meta', text: `${t.id} · created ${when(t.created_at)}${t.finished_at ? ` · finished ${when(t.finished_at)}` : ''}` }),
    outcome ? el('details', { class: 'body' }, el('summary', { text: t.error ? 'Error' : 'Result' }), el('pre', { text: outcome })) : null);
}

async function loadActivity() {
  let goals, tasks, runs;
  try {
    [goals, tasks, runs] = await Promise.all([api('/goals'), api('/tasks?newest_first=true&limit=300'), api('/runs?limit=25')]);
  } catch (err) {
    if (err.status !== 401) showError(err.message);
    return;
  }
  tasksCache = tasks;
  goalsCache = goals;
  for (const id of [...currentTool.keys()]) {
    if (!tasks.some((t) => t.id === id && t.status === 'running')) currentTool.delete(id);
  }
  renderDoing();

  const byGoal = new Map();
  for (const t of [...tasks].reverse()) {
    if (!t.goal_id) continue;
    if (!byGoal.has(t.goal_id)) byGoal.set(t.goal_id, []);
    byGoal.get(t.goal_id).push(t);
  }
  $('goals').replaceChildren(...goals.map((g) => {
    const actions = el('div', { class: 'item-actions' });
    if (!TERMINAL_GOAL.has(g.status)) {
      actions.append(el('button', { type: 'button', class: 'btn btn-small btn-danger', text: 'Stop', 'aria-label': `Stop goal ${g.title}`,
        onclick: (e) => act(e.currentTarget, async () => {
          if (!confirm(`Stop the goal ${q(g.title)} and its unfinished tasks?`)) return;
          await api(`/goals/${g.id}/cancel`, { method: 'POST' });
          await loadActivity();
        }) }));
    }
    const subtasks = byGoal.get(g.id) || [];
    return el('div', { class: 'item', 'data-testid': 'goal', dataset: { id: g.id, status: g.status } },
      el('div', { class: 'item-head' }, statusBadge(g.status), el('span', { class: 'title', text: g.title }), actions),
      el('div', { class: 'meta', text: `${g.id} · created ${when(g.created_at)}` }),
      g.plan ? el('p', { class: 'body', text: g.plan.summary }) : null,
      g.error ? el('p', { class: 'error-text', text: g.error }) : null,
      g.result ? el('details', { class: 'body' }, el('summary', { text: 'Result' }), el('pre', { text: g.result })) : null,
      subtasks.length ? el('div', { class: 'subtasks' }, subtasks.map(taskItem)) : null);
  }));
  if (!goals.length) $('goals').append(empty('No goals yet.'));

  const standalone = tasks.filter((t) => !t.goal_id).slice(0, 50);
  $('tasks').replaceChildren(...standalone.map(taskItem));
  if (!standalone.length) $('tasks').append(empty('No other tasks yet.'));

  $('runs').tBodies[0].replaceChildren(...runs.map((r) => el('tr', {},
    el('td', {}, el('code', { text: r.id })), el('td', { text: r.kind }), el('td', { text: r.mode }),
    el('td', {}, statusBadge(r.status)), el('td', { text: String(r.steps) }), el('td', { text: when(r.started_at) }),
    el('td', { class: 'truncate', title: r.error || r.final || '', text: r.error || r.final || '' }))));
}

$('goal-form').addEventListener('submit', (e) => {
  e.preventDefault();
  act(e.submitter, async () => {
    const title = $('goal-title').value.trim();
    await api('/goals', { method: 'POST', body: { description: $('goal-desc').value.trim(), ...(title ? { title } : {}) } });
    e.target.reset();
    await loadActivity();
  });
});

$('task-form').addEventListener('submit', (e) => {
  e.preventDefault();
  act(e.submitter, async () => {
    await api('/tasks', { method: 'POST', body: {
      title: $('task-title').value.trim(), description: $('task-desc').value.trim(), mode: $('task-mode').value,
      delay_s: Number($('task-delay').value || 0),
    } });
    e.target.reset();
    await loadActivity();
  });
});

for (const b of document.querySelectorAll('[data-refresh]')) {
  b.addEventListener('click', () => act(b, refreshers[b.dataset.refresh]));
}

/* ---------- memory ---------- */

let memoryQuery = '';
const splitTags = (s) => s.split(/[\s,]+/).map((t) => t.trim()).filter(Boolean);

function memoryItem(m) {
  const item = el('div', { class: 'item memory', 'data-testid': 'memory', dataset: { id: String(m.id) } });
  const view = () => {
    item.replaceChildren(
      el('div', { class: 'item-head' }, el('span', { class: 'title', text: m.content }),
        el('div', { class: 'item-actions' },
          el('button', { type: 'button', class: 'btn btn-small', text: 'Edit', 'aria-label': `Edit memory ${m.id}`, onclick: edit }),
          el('button', { type: 'button', class: 'btn btn-small btn-danger', text: 'Forget', 'aria-label': `Forget memory ${m.id}`,
            onclick: (e) => act(e.currentTarget, async () => {
              if (!confirm('Forget this memory? It is deleted from the database and the search index.')) return;
              await api(`/memory/${m.id}`, { method: 'DELETE' });
              await loadMemory();
            }) }))),
      el('div', { class: 'meta', text: `#${m.id} · ${m.kind}${m.tags.length ? ` · ${m.tags.join(', ')}` : ''} · ${m.source || 'unknown source'} · updated ${when(m.updated_at)}` }));
  };
  const edit = () => {
    const ids = { c: `mem-c-${m.id}`, k: `mem-k-${m.id}`, t: `mem-t-${m.id}` };
    const content = el('textarea', { id: ids.c, rows: 3, required: true });
    content.value = m.content;
    const kind = el('input', { id: ids.k, type: 'text', value: m.kind });
    const tags = el('input', { id: ids.t, type: 'text', value: m.tags.join(', ') });
    const form = el('form', { class: 'form' },
      el('label', { for: ids.c }, `Edit memory #${m.id}`, content),
      el('div', { class: 'row' }, el('label', { for: ids.k }, 'Kind', kind), el('label', { for: ids.t }, 'Tags', tags)),
      el('div', { class: 'item-actions' },
        el('button', { type: 'submit', class: 'btn btn-primary btn-small', text: 'Save' }),
        el('button', { type: 'button', class: 'btn btn-small', text: 'Cancel', onclick: view })));
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      act(e.submitter, async () => {
        Object.assign(m, await api(`/memory/${m.id}`, { method: 'PATCH',
          body: { content: content.value.trim(), kind: kind.value.trim() || 'fact', tags: splitTags(tags.value) } }));
        view();
      });
    });
    item.replaceChildren(form);
    content.focus();
  };
  view();
  return item;
}

async function loadMemory() {
  let rows;
  try {
    rows = await api(memoryQuery ? `/memory?q=${encodeURIComponent(memoryQuery)}&limit=100` : '/memory?limit=200');
  } catch (err) {
    if (err.status !== 401) showError(err.message);
    return;
  }
  $('memory-heading').textContent = memoryQuery ? `Memories matching ${q(memoryQuery)}` : `Memories (${rows.length})`;
  $('memories').replaceChildren(...rows.map(memoryItem));
  if (!rows.length) {
    $('memories').append(memoryQuery
      ? empty(`Nothing in Jig\u2019s memory matches ${q(memoryQuery)}.`, 'Try other words, or choose Show all.')
      : empty('Jig hasn\u2019t remembered anything yet.', 'Tell Jig something about yourself in the chat, or add it below.'));
  }
}

$('memory-search').addEventListener('submit', (e) => {
  e.preventDefault();
  memoryQuery = $('memory-q').value.trim();
  act(e.submitter, loadMemory);
});
$('memory-clear').addEventListener('click', () => {
  memoryQuery = '';
  $('memory-q').value = '';
  act($('memory-clear'), loadMemory);
});
$('memory-add').addEventListener('submit', (e) => {
  e.preventDefault();
  act(e.submitter, async () => {
    await api('/memory', { method: 'POST', body: { content: $('memory-content').value.trim(),
      kind: $('memory-kind').value.trim() || 'fact', tags: splitTags($('memory-tags').value) } });
    $('memory-content').value = '';
    $('memory-tags').value = '';
    await loadMemory();
  });
});

/* ---------- what Jig can do on its own (rules) ---------- */

const CAN = {
  web_fetch: 'Read web pages', list_files: 'See which files are in its workspace', read_file: 'Read files in its workspace',
  write_file: 'Save files in its workspace', note_write: 'Write private notes', note_list: 'Look through its notes',
  memory_search: 'Look up what it remembers', memory_add: 'Remember things about you', memory_forget: 'Forget memories',
  current_time: 'Check the time', browser_open: 'Open web pages in its browser', browser_read: 'Read pages in its browser',
  browser_screenshot: 'Take screenshots of web pages', browser_click: 'Click on web pages', browser_type: 'Type into web pages',
  browser_fill: 'Fill in forms on web pages', browser_submit: 'Submit forms on web pages', browser_login: 'Sign in to websites',
  run_command: 'Run commands in its sandbox', run_python: 'Run Python code in its sandbox',
};
const CHOICE_TEXT = { allow: 'On its own', ask: 'Ask me first', block: 'Never' };
const CORE_WORDS = { block: 'Never allowed', ask: 'Always asks you' };
const globRe = (g) => new RegExp(`^${g.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`);
let toolsCache = null;

function choiceRow(tool, custom) {
  const exact = custom.filter((r) => r.enabled && r.tool === tool.name && !r.arg && !r.pattern)
    .sort((a, b) => b.priority - a.priority)[0];
  const others = custom.filter((r) => r.enabled && r !== exact && globRe(r.tool).test(tool.name));
  const current = exact ? exact.decision : tool.default_decision;
  const id = `choice-${tool.name}`;
  const hintId = `${id}-hint`;
  const notes = [];
  if (tool.human_only) notes.push('A core rule means Jig always asks you first.');
  if (others.length) notes.push(`An advanced rule also applies (${others.map((r) => `${r.tool} \u2192 ${r.decision}`).join(', ')}).`);
  const select = el('select', { id, 'aria-describedby': hintId, 'data-testid': 'tool-choice', dataset: { tool: tool.name } },
    ['allow', 'ask', 'block'].map((d) => el('option', { value: d, selected: d === current,
      text: `${CHOICE_TEXT[d]}${d === tool.default_decision ? ' (usual)' : ''}` })));
  const name = CAN[tool.name] || `Use ${tool.name}`;
  select.addEventListener('change', async () => {
    const done = await act(select, async () => {
      if (exact) await api(`/rules/${exact.id}`, { method: 'PATCH', body: { decision: select.value } });
      else if (select.value !== tool.default_decision) {
        await api('/rules', { method: 'POST', body: { tool: tool.name, decision: select.value, note: 'Set in Settings: What Jig can do on its own' } });
      }
      return true;
    });
    $('choices-saved').textContent = done ? `Saved: ${name}, ${CHOICE_TEXT[select.value].toLowerCase()}.` : '';
    await loadRules(); // always show what the server now holds
  });
  return el('div', { class: 'choice', 'data-testid': 'tool-choice-row', dataset: { tool: tool.name, decision: current } },
    el('div', { class: 'choice-text' },
      el('label', { for: id, class: 'choice-name', text: name }),
      el('p', { id: hintId, class: 'hint-quiet' }, el('code', { text: tool.name }), notes.length ? ` \u00b7 ${notes.join(' ')}` : '')),
    select);
}

function ruleRow(r) {
  const id = (f) => `rule-${f}-${r.id}`;
  const input = (f, v, type = 'text') => el('input', { id: id(f), type, value: v ?? '', 'aria-label': `${f} for rule ${r.tool}` });
  const tool = input('tool', r.tool);
  const decision = el('select', { id: id('decision'), 'aria-label': `Decision for rule ${r.tool}` },
    ['allow', 'ask', 'block'].map((d) => el('option', { value: d, text: d, selected: d === r.decision })));
  const arg = input('argument', r.arg);
  const pattern = input('pattern', r.pattern);
  const priority = input('priority', String(r.priority), 'number');
  const enabled = el('input', { id: id('enabled'), type: 'checkbox', checked: r.enabled, 'aria-label': `Rule ${r.tool} enabled` });
  const note = input('note', r.note);
  return el('tr', {},
    el('td', {}, tool), el('td', {}, decision), el('td', {}, arg), el('td', {}, pattern), el('td', {}, priority),
    el('td', {}, enabled), el('td', {}, note),
    el('td', {}, el('div', { class: 'item-actions' },
      el('button', { type: 'button', class: 'btn btn-small', text: 'Save', 'aria-label': `Save rule ${r.tool}`,
        onclick: (e) => act(e.currentTarget, async () => {
          await api(`/rules/${r.id}`, { method: 'PATCH', body: {
            tool: tool.value.trim(), decision: decision.value, arg: arg.value.trim() || null,
            pattern: pattern.value.trim() || null, priority: Number(priority.value || 0), enabled: enabled.checked,
            note: note.value.trim() || null,
          } });
          await loadRules();
        }) }),
      el('button', { type: 'button', class: 'btn btn-small btn-danger', text: 'Delete', 'aria-label': `Delete rule ${r.tool}`,
        onclick: (e) => act(e.currentTarget, async () => {
          if (!confirm(`Delete the rule for ${r.tool}?`)) return;
          await api(`/rules/${r.id}`, { method: 'DELETE' });
          await loadRules();
        }) }))));
}

async function loadRules() {
  let core, custom, tools;
  try {
    [core, custom, tools] = await Promise.all([api('/rules/core'), api('/rules'), toolsCache || api('/tools')]);
  } catch (err) {
    if (err.status !== 401) showError(err.message);
    return;
  }
  toolsCache = tools;
  $('tool-choices').replaceChildren(...tools.map((t) => choiceRow(t, custom)));
  $('core-rules').replaceChildren(...core.map((r) => el('li', { 'data-testid': 'core-rule', dataset: { id: r.id } },
    el('span', { class: 'status', dataset: { s: r.decision }, text: CORE_WORDS[r.decision] || r.decision }), ' ', r.description)));
  const body = $('rules').tBodies[0];
  body.replaceChildren(...custom.map(ruleRow));
  if (!custom.length) body.append(el('tr', {}, el('td', { colspan: '8' }, empty('No custom rules yet, so each tool uses its usual decision.'))));
}

$('rule-form').addEventListener('submit', (e) => {
  e.preventDefault();
  act(e.submitter, async () => {
    await api('/rules', { method: 'POST', body: {
      tool: $('rule-tool').value.trim(), decision: $('rule-decision').value, arg: $('rule-arg').value.trim() || null,
      pattern: $('rule-pattern').value.trim() || null, priority: Number($('rule-priority').value || 0),
      note: $('rule-note').value.trim() || null,
    } });
    e.target.reset();
    await loadRules();
  });
});

/* ---------- history: the audit log ---------- */

let auditOldest = null;
let auditPaged = false;

function auditRow(a) {
  const extra = { ...a.data };
  if (a.task_id) extra.task_id = a.task_id;
  if (a.run_id) extra.run_id = a.run_id;
  return el('details', { class: 'audit-row' },
    el('summary', {}, el('span', { text: `#${a.id}` }), el('span', { text: when(a.ts) }), el('span', { class: 'kind', text: a.kind }),
      el('span', { text: a.actor }), el('span', { text: a.summary })),
    el('pre', { text: json(extra) }));
}

async function loadAudit(reset) {
  const params = new URLSearchParams({ newest_first: 'true', limit: '100' });
  const kind = $('audit-kind').value.trim();
  const task = $('audit-task').value.trim();
  if (kind) params.set('kind', kind);
  if (task) params.set('task_id', task);
  if (!reset && auditOldest !== null) params.set('before_id', String(auditOldest));
  let rows;
  try {
    rows = await api(`/audit?${params}`);
  } catch (err) {
    if (err.status !== 401) showError(err.message);
    return;
  }
  const list = $('audit');
  if (reset) {
    list.replaceChildren();
    auditPaged = false;
  } else {
    auditPaged = true;
  }
  list.append(...rows.map(auditRow));
  if (reset && !rows.length) list.append(empty('No audit entries match those filters.'));
  if (rows.length) auditOldest = rows[rows.length - 1].id;
  $('audit-more').hidden = rows.length < 100;
}

$('audit-form').addEventListener('submit', (e) => {
  e.preventDefault();
  act(e.submitter, () => loadAudit(true));
});
$('audit-more').addEventListener('click', () => act($('audit-more'), () => loadAudit(false)));

/* ---------- use Jig from your other devices (Tailscale) ---------- */

const onHost = () => session.source !== 'tailnet';
let devicesCache = [];
const pairing = { timer: 0, watch: 0 };

async function loadRemote() {
  const box = $('remote-state');
  let r;
  try {
    r = await api('/remote');
  } catch (err) {
    if (err.status !== 401) box.replaceChildren(el('p', { class: 'error-text', text: err.message }));
    return;
  }
  loadDevices();
  setPairingAvailability();
  if (r.applicable === false) {
    box.replaceChildren(
      el('dl', { class: 'kv' }, el('dt', { text: 'Remote access' }),
        el('dd', { 'data-testid': 'remote-status', text: r.hostname ? `On at https://${r.hostname}, through tailscale serve on the host` : 'Off' })),
      el('p', { class: 'hint', text: r.reason }));
    return;
  }
  const ts = r.tailscale;
  const tsText = !ts.installed ? 'Not installed'
    : !ts.signed_in ? `Installed${ts.version ? ` (${ts.version})` : ''}, not signed in`
      : `Signed in as ${ts.login}${ts.tailnet ? ` on ${ts.tailnet}` : ''}`;
  const parts = [el('dl', { class: 'kv' },
    el('dt', { text: 'Tailscale' }), el('dd', { 'data-testid': 'remote-tailscale', text: tsText }),
    el('dt', { text: 'Remote access' }),
    el('dd', { 'data-testid': 'remote-status' }, r.enabled ? ['On at ', el('a', { href: r.url, text: r.url, target: '_blank', rel: 'noopener' })] : 'Off'),
    r.enabled ? [el('dt', { text: 'Who can get in' }),
      el('dd', { text: `${r.allowed_logins.join(', ') || 'nobody'}, from a paired device` })] : null)];
  if (r.problems.length) {
    parts.push(el('div', { class: 'problems', role: 'alert', 'data-testid': 'remote-problems' },
      el('p', { class: 'error-text', text: 'Something needs your attention:' }),
      el('ul', {}, r.problems.map((p) => el('li', { text: p })))));
  }
  if (!r.enabled && r.steps.length) {
    parts.push(el('div', { class: 'card-soft', 'data-testid': 'remote-steps' },
      el('p', { class: 'hint', text: `${ts.error ? `${ts.error}. ` : ''}To get it ready:` }),
      el('ol', { class: 'steps' }, r.steps.map((s) => el('li', { text: s }))),
      el('p', { class: 'hint-quiet', text: 'Jig never installs Tailscale or signs in for you. Choose Refresh when you\u2019ve done these.' })));
  }
  if (r.enabled) {
    parts.push(el('div', { class: 'row wrap' }, el('button', {
      type: 'button', class: 'btn', 'data-testid': 'remote-disable', text: 'Turn remote access off\u2026',
      onclick: (e) => disableRemote(e.currentTarget),
    })));
  } else {
    parts.push(el('div', { class: 'row wrap' }, el('button', {
      type: 'button', class: 'btn btn-primary', 'data-testid': 'remote-enable', text: 'Turn remote access on\u2026',
      disabled: !ts.ready || !onHost(), onclick: (e) => enableRemote(e.currentTarget, r),
    })));
    if (!onHost()) parts.push(el('p', { class: 'hint-quiet', text: 'Turning remote access on only works on the computer Jig runs on.' }));
  }
  box.replaceChildren(...parts);
}

async function enableRemote(button, r) {
  const who = r.allowed_logins.length ? r.allowed_logins.join(', ') : `you (${r.tailscale.login})`;
  const ok = await askConfirm({
    title: 'Turn on remote access?',
    body: [bullets([
      `Jig asks Tailscale to serve it at https://${r.tailscale.dns_name}, for devices on your own tailnet only.`,
      'It is never put on the public internet: Jig refuses Tailscale Funnel.',
      `Who gets in: ${who}, and only from a device you pair here with a one-time code.`,
      'Jig itself keeps listening on this computer only, and nothing else in your Tailscale settings changes.',
      'You can turn it off again here at any time.',
    ]), el('p', { class: 'hint-quiet', text: `Runs: tailscale serve --bg --https=443 http://127.0.0.1:${r.port}` })],
    ok: 'Turn on',
  });
  if (!ok) return;
  await act(button, () => api('/remote/enable', { method: 'POST', body: { confirm: true } }));
  await loadRemote();
}

async function disableRemote(button) {
  const ok = await askConfirm({
    title: 'Turn off remote access?',
    body: [bullets([
      'Your other devices can\u2019t reach Jig until you turn it on again. They stay paired; remove any you no longer use below.',
      'Jig removes only its own tailscale serve entry.',
      onHost() ? null : 'This device loses its connection straight away.',
    ])],
    ok: 'Turn off',
  });
  if (!ok) return;
  const out = await act(button, () => api('/remote/disable', { method: 'POST', body: { confirm: true } }));
  if (out && out.notes.length) showError(out.notes.join(' '));
  if (out && onHost()) await loadRemote();
}

async function loadDevices() {
  try {
    devicesCache = await api('/devices');
  } catch (err) {
    if (err.status !== 401) $('device-list').replaceChildren(el('p', { class: 'error-text', text: err.message }));
    return;
  }
  renderDevices();
}

function renderDevices() {
  $('device-list').replaceChildren(...(devicesCache.length ? devicesCache.map(deviceItem)
    : [empty('No devices paired yet.', 'Add one below, then use Jig from it through Tailscale.')]));
}

function deviceItem(d) {
  const facts = [
    `Paired ${when(d.created_at)}`,
    d.last_used_at ? `last used ${when(d.last_used_at)}` : 'not used yet',
    d.expires_at ? `${d.expired ? 'expired' : 'until'} ${when(d.expires_at)}` : null,
    d.tailscale_login ? `Tailscale user ${d.tailscale_login}` : null,
  ].filter(Boolean).join(' \u00b7 ');
  return el('div', { class: 'item', 'data-testid': 'device-item', dataset: { id: d.id } },
    el('div', { class: 'item-head' },
      el('span', { class: 'title', text: d.name }),
      d.current ? el('span', { class: 'status', dataset: { s: 'active' }, text: 'this device' }) : null,
      d.expired ? el('span', { class: 'status', dataset: { s: 'failed' }, text: 'expired' }) : null,
      el('div', { class: 'item-actions' }, el('button', {
        type: 'button', class: 'btn btn-small btn-danger', 'data-testid': 'device-remove', text: 'Remove\u2026',
        onclick: (e) => removeDevice(e.currentTarget, d),
      }))),
    el('p', { class: 'hint-quiet body', text: facts }));
}

async function removeDevice(button, d) {
  const ok = await askConfirm({
    title: `Remove ${q(d.name)}?`,
    body: [bullets([
      'It\u2019s signed out straight away and can\u2019t get back in without pairing again.',
      d.current ? 'This is the device you\u2019re using now, so you\u2019ll be signed out.' : null,
    ])],
    ok: 'Remove', danger: true,
  });
  if (!ok) return;
  const out = await act(button, () => api(`/devices/${encodeURIComponent(d.id)}`, { method: 'DELETE' }));
  if (!out) return;
  if (d.current) signedOut(`${q(d.name)} was removed, so this device is signed out.`);
  else await loadDevices();
}

function setPairingAvailability() {
  const host = onHost();
  $('pairing-new').disabled = !host;
  $('pairing-expiry').disabled = !host;
  $('pairing-hint').textContent = host
    ? 'Shows a one-time code and a QR code. On the new device, scan it or open the link, then give the device a name. The code works once, for 5 minutes.'
    : 'Adding a device only works on the computer Jig runs on: open Settings there and choose Add a device.';
}

function stopPairing() {
  clearInterval(pairing.timer);
  clearInterval(pairing.watch);
  pairing.timer = 0;
  pairing.watch = 0;
  $('pairing-code').hidden = true;
  $('pairing-controls').hidden = false;
  $('pairing-qr').removeAttribute('src');
}

function showPairing(p) {
  stopPairing();
  $('pairing-saved').textContent = '';
  $('pairing-qr').src = p.qr_svg_data_uri;
  $('pairing-display').textContent = p.display_code;
  $('pairing-url').textContent = p.url;
  $('pairing-local').hidden = p.remote_enabled;
  $('pairing-controls').hidden = true;
  $('pairing-code').hidden = false;
  const ends = Date.now() + p.expires_in * 1000;
  const tick = () => {
    const left = Math.max(0, Math.round((ends - Date.now()) / 1000));
    if (!left) {
      stopPairing();
      $('pairing-saved').textContent = '';
      $('pairing-hint').textContent = 'That code has expired. Choose Add a device for a new one.';
      return;
    }
    $('pairing-left').textContent = `Works once, for another ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}.`;
  };
  tick();
  pairing.timer = setInterval(tick, 1000);
  const known = new Set(devicesCache.map((d) => d.id));
  pairing.watch = setInterval(async () => {
    let list;
    try {
      list = await api('/devices');
    } catch (err) {
      clearInterval(pairing.watch);
      if (err.status !== 401) showError(`Jig couldn\u2019t check for the new device: ${err.message}`);
      return;
    }
    const fresh = list.find((d) => !known.has(d.id));
    if (!fresh) return;
    stopPairing();
    devicesCache = list;
    renderDevices();
    setPairingAvailability();
    $('pairing-saved').textContent = `${q(fresh.name)} is paired.`;
  }, 3000);
}

$('pairing-new').addEventListener('click', () => act($('pairing-new'), async () => {
  const days = $('pairing-expiry').value;
  await loadDevices();
  showPairing(await api('/devices/pairing', { method: 'POST', body: days ? { expires_in_days: Number(days) } : {} }));
}));
$('pairing-cancel').addEventListener('click', () => {
  stopPairing();
  setPairingAvailability();
  loadDevices();
});
$('remote-refresh').addEventListener('click', () => act($('remote-refresh'), loadRemote));

/* ---------- turning Jig off ---------- */

let powerCache = null;

function gpuText(m) {
  if (m.stopped_by_user) return 'None: the model server Jig started is stopped.';
  if (!m.managed) return 'Not affected: Jig didn\u2019t start the model server, so it never stops it.';
  const g = m.gpu;
  if (!g.available) return g.reason;
  if (!g.on_gpu) return g.note;
  const cards = g.gpus.map((c) => `${c.name}: ${c.memory_used_mib.toLocaleString('en-GB')} of ${c.memory_total_mib.toLocaleString('en-GB')} MiB in use`).join('; ');
  return g.vram_mib !== null
    ? `The model server holds ${g.vram_mib.toLocaleString('en-GB')} MiB. ${cards}.`
    : `${g.note} ${cards}.`;
}

async function loadPower() {
  let p;
  try {
    p = await api('/power');
  } catch (err) {
    if (err.status !== 401) $('pw-model').textContent = `Jig couldn\u2019t check: ${err.message}`;
    return;
  }
  powerCache = p;
  const na = p.applicable === false;
  for (const id of ['power-state', 'power-actions', 'power-model-note', 'model-advanced']) $(id).hidden = na;
  $('power-na').hidden = !na;
  if (na) {
    $('power-na').textContent = p.reason;
    return;
  }
  const m = p.model_server;
  $('pw-model').textContent = m.managed
    ? `Started by Jig (process ${m.pid})${m.adopted ? ', and picked up again after Jig restarted' : ''}.`
    : m.refusal;
  $('pw-gpu').textContent = gpuText(m);
  $('pw-again').textContent = p.start_again;
  $('power-off').disabled = false;
  $('power-off-model').disabled = !p.can_stop_model;
  $('power-model-note').textContent = p.can_stop_model ? ''
    : 'Turning the model off too is only possible for a model server that Jig started itself.';
  $('model-stop').hidden = !m.managed;
  $('model-start').hidden = m.managed || !m.configured;
}

async function turnOff(button, scope) {
  const p = powerCache;
  const m = p.model_server;
  const withModel = scope === 'jig_and_model';
  const ok = await askConfirm({
    title: withModel ? 'Turn Jig and the model off?' : 'Turn Jig off?',
    body: [bullets([
      'Jig finishes the step it\u2019s on, saves where it got to, and picks up unfinished work next time it starts.',
      withModel ? 'The model server Jig started is stopped too, which frees its GPU memory.'
        : m.managed ? 'The model server keeps running, and keeps its GPU memory. Next time Jig starts, it picks it up again.'
          : 'Your model server isn\u2019t Jig\u2019s, so it\u2019s left as it is.',
      p.start_again,
      onHost() ? null : 'You can\u2019t start Jig again from this device: that has to be done on the computer it runs on.',
    ])],
    ok: 'Turn off', danger: withModel,
  });
  if (!ok) return;
  ownStop = true;
  const r = await act(button, () => api('/power/stop', { method: 'POST', body: { scope, confirm: true } }));
  if (!r) {
    ownStop = false;
    return;
  }
  showOff({ scope, startAgain: r.start_again, modelWasManaged: m.managed });
}

$('power-off').addEventListener('click', () => turnOff($('power-off'), 'jig'));
$('power-off-model').addEventListener('click', () => turnOff($('power-off-model'), 'jig_and_model'));
$('power-refresh').addEventListener('click', () => act($('power-refresh'), loadPower));

$('model-stop').addEventListener('click', async () => {
  const ok = await askConfirm({
    title: 'Stop the model server?',
    body: [bullets([
      'Jig keeps running, but it can\u2019t answer or work on anything until the model server is back.',
      'This frees whatever GPU memory the model server holds.',
      'Start it again here, or with jig model start.',
    ])],
    ok: 'Stop it', danger: true,
  });
  if (!ok) return;
  const r = await act($('model-stop'), () => api('/model/stop', { method: 'POST', body: { confirm: true } }));
  if (!r) return;
  $('model-saved').textContent = `The model server is stopped (process ${r.pid}).`;
  await loadPower();
  loadStatus();
});

$('model-start').addEventListener('click', () => act($('model-start'), async () => {
  $('model-saved').textContent = 'Starting the model server. Loading a model can take a minute\u2026';
  let r;
  try {
    r = await api('/model/start', { method: 'POST' });
  } catch (err) {
    $('model-saved').textContent = '';
    throw err;
  }
  $('model-saved').textContent = r.started ? `The model server is running again (process ${r.pid}).` : 'The model server was already running.';
  await loadPower();
  loadStatus();
}));

/* ---------- "Jig is off" ---------- */

const OFF_GENERIC = 'To start it again, run jig serve on the computer Jig runs on. If \u2018Start with Windows\u2019 is on, it also starts by itself when you next sign in.';

function showOff({ scope, startAgain, fromElsewhere = false, modelWasManaged = false }) {
  if (startAgain) $('off-again').textContent = startAgain;
  if (offState) return;
  offState = { scope, modelWasManaged, since: Date.now(), phase: 'stopping' };
  stopEvents();
  stopPairing();
  for (const d of document.querySelectorAll('dialog[open]')) d.close();
  $('app').hidden = true;
  $('open-settings').hidden = true;
  renderHealth();
  $('off-state').hidden = false;
  $('off-title').textContent = 'Turning Jig off\u2026';
  $('off-text').textContent = fromElsewhere ? 'Jig is being turned off from another device or window.' : 'Finishing up and saving where it got to.';
  if (!startAgain) $('off-again').textContent = OFF_GENERIC;
  $('off-watch').textContent = '';
  $('off-title').focus();
  watchOff();
}

async function jigAnswers() {
  try {
    const r = await fetch('/health', { cache: 'no-store', credentials: 'same-origin' });
    return r.ok;
  } catch {
    return false;
  }
}

async function watchOff() {
  const up = await jigAnswers();
  if (offState.phase === 'stopping') {
    if (!up) {
      offState.phase = 'off';
      $('off-title').textContent = 'Jig is off';
      $('off-text').textContent = offState.scope === 'jig_and_model' ? 'Jig and the model server it started are off.'
        : offState.modelWasManaged ? 'Jig is off. The model server it started is still running.' : 'Jig is off.';
      $('off-watch').textContent = 'This page reconnects by itself when Jig is running again.';
      $('off-watch').classList.remove('error-text');
    } else if (Date.now() - offState.since > 120000) {
      $('off-watch').textContent = 'Jig said it was turning off, but it is still answering after 2 minutes. Check its window or log on the computer it runs on.';
      $('off-watch').classList.add('error-text');
    }
  } else if (up) {
    $('off-watch').textContent = 'Jig is back. Reconnecting\u2026';
    location.reload();
    return;
  }
  setTimeout(watchOff, offState.phase === 'stopping' ? 1000 : 5000);
}

boot();
