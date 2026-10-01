/*
 * Jig web UI. Dependency-free; talks only to the Jig runtime that serves it.
 * Authentication is a same-origin HttpOnly session cookie, so this script never sees the API token.
 * Model output and web-derived text are always inserted as text, never as HTML.
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
  for (const c of children.flat()) {
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
const json = (v) => JSON.stringify(v, null, 2);
const face = (cls = 'face') => el('img', { class: cls, src: '/web/jig-face.svg', alt: '', width: 40, height: 40 });
const empty = (text, more) => el('div', { class: 'empty-state' }, face('face face-empty'),
  el('div', {}, el('p', { class: 'empty', text }), more ? el('p', { class: 'hint', text: more }) : null));

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

/** Run an action from a button, disabling it meanwhile and surfacing any error. */
async function act(button, fn) {
  if (button) button.disabled = true;
  try {
    return await fn();
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 401)) showError(err.message);
    return undefined;
  } finally {
    if (button) button.disabled = false;
  }
}

/* ---------- sign-in ---------- */

let started = false;

async function boot() {
  let loginError = '';
  const m = location.hash.match(/^#code=([A-Za-z0-9_-]+)$/);
  if (m) {
    history.replaceState(null, '', location.pathname + location.search); // drop the one-time code at once
    try {
      await api('/auth/session', { method: 'POST', body: { code: m[1] } });
    } catch (err) {
      loginError = `That sign-in link did not work: ${err.message.replace(/^POST \/auth\/session: /, '')}`;
    }
  }
  let session;
  try {
    session = await api('/auth/session');
  } catch (err) {
    showError(err.message);
    setConn('Unreachable', 'bad');
    return;
  }
  if (session.authenticated) start();
  else signedOut(loginError);
}

function signedOut(message = '') {
  started = false;
  stopEvents();
  $('app').hidden = true;
  $('logout').hidden = true;
  $('agent-toggle').disabled = true;
  setConn('Signed out', 'bad');
  $('login-error').textContent = message;
  const dialog = $('login');
  if (!dialog.open) dialog.showModal();
  $('login-token').focus();
}

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

$('logout').addEventListener('click', () => act($('logout'), async () => {
  await api('/auth/logout', { method: 'POST' });
  signedOut('Signed out.');
}));

function start() {
  if (started) return;
  started = true;
  $('app').hidden = false;
  $('logout').hidden = false;
  connectEvents();
  refreshAll();
}

function refreshAll() {
  loadStatus();
  loadActivity();
  loadApprovals();
  loadMemory();
  loadRules();
  loadAudit(true);
}

/* ---------- tabs (WAI-ARIA tabs pattern, automatic activation) ---------- */

const tabs = [...document.querySelectorAll('[role="tab"]')];

function selectTab(tab, focus = true) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute('aria-selected', String(on));
    t.tabIndex = on ? 0 : -1;
    $(t.getAttribute('aria-controls')).hidden = !on;
  }
  if (focus) tab.focus();
  if (tab.id === 'tab-audit') loadAudit(true);
}

for (const tab of tabs) {
  tab.addEventListener('click', () => selectTab(tab));
  tab.addEventListener('keydown', (e) => {
    const i = tabs.indexOf(tab);
    const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    selectTab(tabs[(next + tabs.length) % tabs.length]);
  });
}

const panelVisible = (id) => !$(id).hidden;

/* ---------- live events and the avatar ---------- */

let ws = null;
let wsRetry = 0;
let wsTimer = 0;
let wsWanted = false;
const avatars = [$('avatar'), $('mini-avatar'), $('companion-avatar')];

function setConn(text, tone) {
  const pill = $('conn');
  pill.textContent = text;
  pill.dataset.tone = tone;
  $('st-conn').textContent = text;
}

function connectEvents() {
  wsWanted = true;
  clearTimeout(wsTimer);
  setConn('Connecting…', 'warn');
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
      const session = await api('/auth/session');
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
    for (const a of avatars) a.setState(d.state, opts);
  } catch (err) {
    showError(`The avatar could not show state "${d.state}": ${err.message}`);
    return;
  }
  $('avatar-state').textContent = d.state;
  $('avatar-task').textContent = d.state === 'working' && d.variant ? `: ${d.variant}` : '';
  $('avatar-bg').hidden = !background;
  const words = avatarWords(d.state, d.variant, background);
  $('avatar-says').textContent = words;
  $('companion-says').textContent = words;
  document.documentElement.dataset.jigState = d.state;
}

const WORKING_WORDS = {
  browsing: 'Jig is reading and looking things up.',
  writing: 'Jig is writing.',
  coding: 'Jig is running some code.',
  shopping: 'Jig is working on a purchase.',
  scheduling: 'Jig is checking the time or your calendar.',
};
const STATE_WORDS = {
  idle: 'Jig is here and ready when you are.',
  monitoring: 'Jig is keeping an eye on things in the background.',
  thinking: 'Jig is thinking\u2026',
  talking: 'Jig is talking.',
  approval: 'Jig needs your OK before carrying on. See Approvals.',
  success: 'All done! Jig finished that.',
  error: 'Something went wrong. The details are on screen.',
  paused: 'Jig is paused. Nothing new starts until you resume.',
};

/** The avatar's state in plain words, for everyone and for screen readers. */
function avatarWords(state, variant, background) {
  let words = state === 'working' ? WORKING_WORDS[variant] : STATE_WORDS[state];
  if (!words) words = `Jig is ${state}${variant ? `: ${variant}` : ''}.`;
  return background ? `${words.replace(/\.$/, '')}, quietly in the background (read-only).` : words;
}

const refreshers = { activity: loadActivity, approvals: loadApprovals, memory: loadMemory, rules: loadRules,
  status: loadStatus, audit: () => loadAudit(true) };
const pending = {};
function refreshSoon(area, delay = 300) {
  clearTimeout(pending[area]);
  pending[area] = setTimeout(() => refreshers[area](), delay);
}

function handleEvent(event) {
  const t = event.type;
  if (t === 'avatar.state') {
    applyAvatar(event.data);
    return;
  }
  if (t === 'task.status' || t === 'goal.status' || t === 'run.start' || t === 'run.end') refreshSoon('activity');
  if (t === 'approval.requested' || t === 'approval.resolved') {
    refreshSoon('approvals');
    refreshSoon('activity');
  }
  if (t === 'memory.changed') refreshSoon('memory');
  if (t === 'rule.changed') refreshSoon('rules');
  if (t === 'agent.status') {
    setAgentPaused(event.data.paused);
    refreshSoon('status');
  }
  if (panelVisible('panel-audit') && !auditPaged) refreshSoon('audit', 1000);
}

if (!STATES.includes('paused')) showError('This avatar component does not support the paused state.');

/* ---------- status and agent pause ---------- */

let agentPaused = false;

function setAgentPaused(paused) {
  agentPaused = paused;
  const b = $('agent-toggle');
  b.disabled = false;
  b.textContent = paused ? 'Resume agent' : 'Pause agent';
  b.setAttribute('aria-pressed', String(paused));
  b.classList.toggle('btn-warn', paused);
  $('st-agent').textContent = paused ? 'Paused: no schedules fire and no tasks start' : 'Running';
}

$('agent-toggle').addEventListener('click', () => act($('agent-toggle'), async () => {
  const status = await api(agentPaused ? '/agent/resume' : '/agent/pause', { method: 'POST' });
  setAgentPaused(status.paused);
}));

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
  try {
    const s = await api('/status');
    $('st-endpoint').textContent = s.model_endpoint;
    $('st-model').textContent = s.model.model;
    $('st-context').textContent = s.model.context_tokens ? `${s.model.context_tokens.toLocaleString('en-GB')} tokens` : 'not reported';
    $('st-caps').textContent = capsText(s.capabilities.agent);
    $('st-sentinel').textContent = `${s.sentinel_model.model}: ${capsText(s.capabilities.sentinel)}`;
    $('st-vault').textContent = s.vault_backend;
    setAgentPaused(s.agent.paused);
  } catch (err) {
    if (err.status === 401) return;
    $('st-model').textContent = 'unavailable';
    showError(err.message);
  }
}
$('status-refresh').addEventListener('click', () => act($('status-refresh'), loadStatus));

/* ---------- chat ---------- */

let sessionId = null;
let chatBusy = false;
const chatLog = $('chat-log');

function nearBottom() {
  return chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 80;
}

function welcome() {
  return el('div', { class: 'welcome', 'data-testid': 'chat-welcome' }, face('face face-welcome'),
    el('div', {},
      el('p', { class: 'welcome-title', text: 'Hello! What shall we do?' }),
      el('p', { class: 'hint', text: 'Ask a question or give Jig something to do. Actions that change, send or spend anything '
        + 'are checked against your rules and by the Sentinel, and anything that needs your OK waits for you in Approvals.' })));
}

function addMessage(role, text = '') {
  chatLog.querySelector('.welcome')?.remove();
  const content = el('div', { class: 'content', text });
  const msg = el('article', { class: `msg ${role}`, 'aria-label': role === 'user' ? 'You said' : 'Jig replied',
    'data-testid': role === 'user' ? 'chat-user' : 'chat-jig' },
    el('div', { class: 'who' }, role === 'user' ? null : face('face face-who'), role === 'user' ? 'You' : 'Jig'), content);
  chatLog.append(msg);
  chatLog.scrollTop = chatLog.scrollHeight;
  return { msg, content };
}

const VERDICT_WORDS = { allow: 'looks fine', ask_user: 'ask you first', deny: 'do not do this' };

function describeChatEvent(ev) {
  const d = ev.data;
  switch (ev.type) {
    case 'tool.start': return `Using ${d.tool} (${d.variant})`;
    case 'tool.end': return d.ok ? null : `${d.tool} didn\u2019t complete`;
    case 'sentinel.verdict':
      return `Sentinel checked ${d.tool}: ${VERDICT_WORDS[d.verdict] || d.verdict} (${d.verdict}), ${d.risk} risk. ${d.reason}`;
    case 'approval.requested': return d.resumed ? null : `Jig would like your OK to use ${d.tool}`;
    case 'approval.resolved': return `You ${d.status === 'approved' ? 'approved' : d.status === 'denied' ? 'denied' : `answered (${d.status})`} ${d.tool}`;
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
  let events = null;
  let finished = false;

  const fail = (text) => {
    msg.classList.add('error');
    msg.append(el('p', { class: 'error-text', text: `Jig couldn\u2019t finish this reply: ${text}` }));
    showError(`Chat: ${text}`);
  };

  const handle = (item) => {
    const follow = nearBottom();
    if (item.type === 'start') {
      sessionId = item.session_id;
    } else if (item.type === 'reasoning') {
      if (!thinking) {
        const pre = el('pre');
        thinking = { pre, box: el('details', { class: 'thinking' }, el('summary', { text: 'Thinking' }), pre) };
        msg.insertBefore(thinking.box, content);
      }
      thinking.pre.textContent += item.text;
    } else if (item.type === 'content') {
      content.textContent += item.text;
    } else if (item.type === 'event') {
      const text = describeChatEvent(item.event);
      if (text) {
        if (!events) {
          events = el('ul', { class: 'events', 'aria-label': 'What Jig did' });
          msg.append(events);
        }
        const li = el('li', { text });
        if (item.event.type === 'approval.requested') {
          li.classList.add('asks');
          li.append(' ', el('button', { type: 'button', class: 'btn btn-small btn-warn', text: 'Review', 'data-testid': 'chat-review',
            onclick: () => selectTab($('tab-approvals')) }));
        }
        events.append(li);
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
      fail(`unexpected stream item "${item.type}"`);
    }
    if (follow) chatLog.scrollTop = chatLog.scrollHeight;
  };

  try {
    let r;
    try {
      r = await fetch('/chat', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, session_id: sessionId, mode: $('chat-mode').value }),
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
    chatBusy = false;
    $('chat-send').disabled = false;
  }
}

$('chat-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const input = $('chat-input');
  const message = input.value.trim();
  if (!message || chatBusy) return;
  input.value = '';
  sendChat(message);
});
$('chat-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $('chat-form').requestSubmit();
  }
});
$('chat-new').addEventListener('click', () => {
  sessionId = null;
  chatLog.replaceChildren(welcome());
  $('chat-input').focus();
});
chatLog.append(welcome());

/* ---------- activity: goals, tasks, runs ---------- */

function taskItem(t) {
  const actions = el('div', { class: 'item-actions' });
  const label = `task ${t.title}`;
  if (t.status === 'waiting_approval') {
    actions.append(el('button', { type: 'button', class: 'btn btn-small btn-warn', text: 'Review', 'aria-label': `Review ${label}`,
      'data-testid': 'task-review', onclick: () => selectTab($('tab-approvals')) }));
  }
  if (['queued', 'running', 'waiting_approval'].includes(t.status)) {
    actions.append(el('button', { type: 'button', class: 'btn btn-small', text: 'Pause', 'aria-label': `Pause ${label}`,
      onclick: (e) => act(e.currentTarget, async () => { await api(`/tasks/${t.id}/pause`, { method: 'POST' }); loadActivity(); }) }));
  }
  if (t.status === 'paused') {
    actions.append(el('button', { type: 'button', class: 'btn btn-small btn-approve', text: 'Resume', 'aria-label': `Resume ${label}`,
      onclick: (e) => act(e.currentTarget, async () => { await api(`/tasks/${t.id}/resume`, { method: 'POST' }); loadActivity(); }) }));
  }
  if (!TERMINAL_TASK.has(t.status)) {
    actions.append(el('button', { type: 'button', class: 'btn btn-small btn-danger', text: 'Cancel', 'aria-label': `Cancel ${label}`,
      onclick: (e) => act(e.currentTarget, async () => {
        if (!confirm(`Cancel the task "${t.title}"?`)) return;
        await api(`/tasks/${t.id}/cancel`, { method: 'POST' });
        loadActivity();
      }) }));
  }
  const outcome = t.error || t.result;
  return el('div', { class: 'item', 'data-testid': 'task', dataset: { id: t.id, status: t.status } },
    el('div', { class: 'item-head' }, statusBadge(t.status), el('span', { class: 'title', text: t.title }),
      el('span', { class: 'status', dataset: { s: 'mode' }, text: t.mode }), actions),
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
  const byGoal = new Map();
  for (const t of [...tasks].reverse()) {
    if (!t.goal_id) continue;
    if (!byGoal.has(t.goal_id)) byGoal.set(t.goal_id, []);
    byGoal.get(t.goal_id).push(t);
  }
  const goalList = $('goals');
  goalList.replaceChildren(...goals.map((g) => {
    const actions = el('div', { class: 'item-actions' });
    if (!TERMINAL_GOAL.has(g.status)) {
      actions.append(el('button', { type: 'button', class: 'btn btn-small btn-danger', text: 'Cancel', 'aria-label': `Cancel goal ${g.title}`,
        onclick: (e) => act(e.currentTarget, async () => {
          if (!confirm(`Cancel the goal "${g.title}" and its unfinished tasks?`)) return;
          await api(`/goals/${g.id}/cancel`, { method: 'POST' });
          loadActivity();
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
  if (!goals.length) goalList.append(empty('No goals yet.', 'Tell Jig what you\u2019d like to achieve above, and it will plan the steps.'));

  const standalone = tasks.filter((t) => !t.goal_id).slice(0, 50);
  $('tasks').replaceChildren(...standalone.map(taskItem));
  if (!standalone.length) $('tasks').append(empty('No standalone tasks yet.', 'One-off and scheduled tasks you create appear here.'));

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
    loadActivity();
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
    loadActivity();
  });
});

for (const b of document.querySelectorAll('[data-refresh]')) {
  b.addEventListener('click', () => act(b, refreshers[b.dataset.refresh]));
}

/* ---------- approvals ---------- */

/* What each tool would do, in plain words. Every argument is always listed in full as well. */
const ASK = {
  write_file: 'write a file', memory_forget: 'forget a memory', memory_add: 'remember something', note_write: 'save a note',
  web_fetch: 'fetch a web page', browser_open: 'open a web page', browser_click: 'click on a web page',
  browser_type: 'type into a web page', browser_fill: 'fill in a form field', browser_submit: 'submit a form',
  browser_login: 'sign in to a website', run_command: 'run a command', run_python: 'run some Python code',
};
const q = (v) => `\u201c${v}\u201d`;
const WILL = {
  write_file: (x) => `Jig will write ${String(x.content ?? '').length.toLocaleString('en-GB')} characters to the file ${q(x.path)} in its `
    + `workspace${x.overwrite ? ', replacing that file if it already exists.' : '. If that file already exists, nothing is replaced and the step fails.'}`,
  memory_forget: (x) => `Jig will permanently delete memory #${x.memory_id}.`,
  memory_add: (x) => `Jig will remember: ${q(x.content)}.`,
  note_write: (x) => `Jig will save a private note titled ${q(x.title)}.`,
  web_fetch: (x) => `Jig will fetch ${x.url}${x.headers && Object.keys(x.headers).length ? ' with the request headers listed below' : ''}.`,
  browser_open: (x) => `Jig will open ${x.url} in its sandboxed browser.`,
  browser_click: (x) => `Jig will click the element ${q(x.selector)} on the current page.`,
  browser_type: (x) => `Jig will type ${q(x.text)} into ${q(x.selector)}${x.press_enter ? ' and then press Enter' : ''}.`,
  browser_fill: (x) => `Jig will fill ${q(x.selector)} with ${q(x.value)}.`,
  browser_submit: (x) => `Jig will submit the form ${q(x.selector)} on the current page.`,
  browser_login: (x) => `Jig will sign in on the current page as ${q(x.username)}.`,
  run_command: (x) => `Jig will run this command in its sandbox: ${x.command}`,
  run_python: () => 'Jig will run the Python code below in its sandbox.',
};
const VERDICT_LABEL = { allow: 'Looks fine', ask_user: 'Ask you first', deny: 'Do not do this' };
const RISK_MARK = { low: '\u25CF', medium: '\u25B2', high: '\u25A0' };

function argValue(v) {
  if (typeof v === 'boolean') return el('span', { text: v ? 'yes' : 'no' });
  if (v === null || v === undefined) return el('span', { class: 'muted-inline', text: 'not set' });
  if (typeof v === 'object') return el('pre', { class: 'args', text: json(v) });
  const s = String(v);
  return s.length > 80 || s.includes('\n') ? el('pre', { class: 'args', text: s }) : el('code', { text: s });
}

function approvalItem(a) {
  const s = a.sentinel;
  const pendingNow = a.status === 'pending';
  const titleId = `ap-title-${a.id}`;
  const doing = ASK[a.tool] || `use the tool ${a.tool}`;
  const willText = WILL[a.tool] ? WILL[a.tool](a.args) : `Jig will run ${a.tool} with exactly the details below.`;
  const argList = el('dl', { class: 'args-list' }, Object.entries(a.args).flatMap(([k, v]) => [el('dt', { text: k }), el('dd', {}, argValue(v))]));

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

  const why = el('section', { class: 'why', 'aria-label': 'Why you are being asked' },
    el('h4', { text: 'Why you\u2019re being asked' }),
    el('ul', { class: 'reasons' }, a.reasons.map((r) => el('li', {}, el('code', { text: r.rule }), `: ${r.reason}`))));

  const whatBox = el('section', { class: 'will-happen', 'aria-label': 'What will happen', 'data-testid': 'approval-what' },
    el('h4', { text: pendingNow ? 'What will happen if you approve' : 'What Jig asked to do' }),
    el('p', { class: 'will-text', text: willText }),
    pendingNow ? argList : el('details', {}, el('summary', { text: 'All the details' }), argList));

  const parts = [
    el('div', { class: 'approval-head' }, face('face face-ask'),
      el('div', { class: 'approval-heading' },
        el('h3', { id: titleId, class: 'approval-title', text: pendingNow ? `Jig would like your OK to ${doing}` : `Jig asked to ${doing}` }),
        el('p', { class: 'meta', text: `${a.tool} · requested ${when(a.created_at)}${a.resolved_at ? ` · answered ${when(a.resolved_at)}` : ''}` })),
      statusBadge(a.status)),
    whatBox, verdict, why,
    el('details', { class: 'tech' }, el('summary', { text: 'Technical details' }),
      el('p', { class: 'meta', text: `Approval ${a.id} · task ${a.task_id || '—'} · tool ${a.tool}` }),
      el('pre', { class: 'args', text: json(a.args) })),
  ];
  if (a.note) parts.push(el('p', { class: 'meta', text: `Your note: ${a.note}` }));
  if (pendingNow) {
    const noteId = `note-${a.id}`;
    const note = el('input', { id: noteId, type: 'text', placeholder: 'For example, why you said yes or no', 'data-testid': 'approval-note' });
    const answer = (approve) => (e) => act(e.currentTarget, async () => {
      await api(`/approvals/${a.id}`, { method: 'POST', body: { approve, note: note.value.trim() || null } });
      loadApprovals();
    });
    parts.push(el('div', { class: 'decide' },
      el('label', { for: noteId }, 'Add a note (optional)', note),
      el('div', { class: 'decide-buttons' },
        el('button', { type: 'button', class: 'btn btn-approve', text: 'Approve', 'aria-label': `Approve ${a.tool}`,
          'data-testid': 'approval-approve', onclick: answer(true) }),
        el('button', { type: 'button', class: 'btn btn-deny', text: 'Deny', 'aria-label': `Deny ${a.tool}`,
          'data-testid': 'approval-deny', onclick: answer(false) }))));
  }
  return el('article', { class: `item approval${pendingNow ? ' is-pending' : ''}`, 'aria-labelledby': titleId,
    'data-testid': 'approval', dataset: { id: a.id, status: a.status } }, parts);
}

async function loadApprovals() {
  let pendingList, all;
  try {
    [pendingList, all] = await Promise.all([api('/approvals?status=pending'), api('/approvals')]);
  } catch (err) {
    if (err.status !== 401) showError(err.message);
    return;
  }
  $('approvals-pending').replaceChildren(...pendingList.map(approvalItem));
  if (!pendingList.length) $('approvals-pending').append(empty('Nothing needs your OK right now.', 'When Jig wants to do something that needs you, it will wait here.'));
  const answered = all.filter((a) => a.status !== 'pending').slice(0, 20);
  $('approvals-done').replaceChildren(...answered.map(approvalItem));
  if (!answered.length) $('approvals-done').append(empty('You haven\u2019t answered any requests yet.'));
  const badge = $('approvals-badge');
  badge.hidden = pendingList.length === 0;
  badge.textContent = String(pendingList.length);
  badge.setAttribute('aria-label', `${pendingList.length} waiting`);
  document.title = pendingList.length ? `(${pendingList.length}) Jig` : 'Jig';
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
              loadMemory();
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
  $('memory-heading').textContent = memoryQuery ? `Memories matching "${memoryQuery}"` : 'Memories';
  $('memories').replaceChildren(...rows.map(memoryItem));
  if (!rows.length) {
    $('memories').append(memoryQuery
      ? empty(`Nothing in Jig\u2019s memory matches ${q(memoryQuery)}.`, 'Try other words, or choose Show all.')
      : empty('Jig hasn\u2019t remembered anything yet.', 'Add a memory above, or tell Jig something about yourself in the chat.'));
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
    loadMemory();
  });
});

/* ---------- rules ---------- */

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
          loadRules();
        }) }),
      el('button', { type: 'button', class: 'btn btn-small btn-danger', text: 'Delete', 'aria-label': `Delete rule ${r.tool}`,
        onclick: (e) => act(e.currentTarget, async () => {
          if (!confirm(`Delete the rule for ${r.tool}?`)) return;
          await api(`/rules/${r.id}`, { method: 'DELETE' });
          loadRules();
        }) }))));
}

async function loadRules() {
  let core, custom;
  try {
    [core, custom] = await Promise.all([api('/rules/core'), api('/rules')]);
  } catch (err) {
    if (err.status !== 401) showError(err.message);
    return;
  }
  $('core-rules').replaceChildren(...core.map((r) => el('li', {}, el('code', { text: r.id }), ' ', statusBadge(r.decision), ' ', r.description)));
  const body = $('rules').tBodies[0];
  body.replaceChildren(...custom.map(ruleRow));
  if (!custom.length) body.append(el('tr', {}, el('td', { colspan: '8' }, empty('No custom rules yet, so each tool uses its default decision.'))));
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
    loadRules();
  });
});

/* ---------- audit ---------- */

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

boot();
