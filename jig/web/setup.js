/*
 * Jig's set-up page: the first run, and changing the model from Settings. Also the Settings controls for cloud
 * keys and consent, and for turning on running code. Talks to /setup/... (jig/api/setup.py).
 *
 * The agent never runs on a model that hasn't passed Jig's checks: choosing a model here runs the same real
 * checks Jig runs at start-up, shows them as they happen, and only switches over when they all pass.
 */

const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'text') node.textContent = v;
    else if (k === 'class') node.className = v;
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

class CallError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function call(path, { method = 'GET', body } = {}) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let r;
  try {
    r = await fetch(path, opts);
  } catch (err) {
    throw new CallError(0, `Jig isn\u2019t answering: ${err.message}`);
  }
  const text = await r.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new CallError(r.status, `Jig sent something unexpected (HTTP ${r.status}).`);
  }
  if (!r.ok) throw new CallError(r.status, (data && data.error) || `HTTP ${r.status}`);
  return data;
}

/** POST a set-up action and call onLine for each progress line (newline-delimited JSON). */
async function stream(path, body, onLine) {
  const r = await fetch(path, {
    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) {
    const data = await r.json().catch(() => null);
    throw new CallError(r.status, (data && data.error) || `HTTP ${r.status}`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let last = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let i;
    while ((i = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, i).trim();
      buffer = buffer.slice(i + 1);
      if (!line) continue;
      last = JSON.parse(line);
      onLine(last);
    }
  }
  return last;
}

const confirmBox = (options) => window.jigConfirm(options);
const kTokens = (n) => (n >= 1024 ? `${Math.round(n / 1024)}K` : String(n));
const ADVISED_CONTEXT = 32768;

/* ---------- the set-up page ---------- */

const CHECK_STEPS = [
  ['loading', 'Loading it in LM Studio with a 32K context'],
  ['reach', 'Reaching the model'],
  ['tools', 'Checking it can use tools'],
  ['structured', 'Checking it can answer in Jig\u2019s format'],
  ['context', 'Checking how much it can keep in mind'],
  ['sentinel', 'Checking the safety checker\u2019s model'],
  ['vision', 'Checking it can see pictures'],
  ['starting', 'Starting Jig'],
];

const page = {
  mode: 'setup', // 'setup': the agent is off; 'change': opened from Settings while Jig runs
  status: null,
  avatar: null,
  servers: null,
  gpu: null,
  providers: null,
  lastChoice: null,
  checking: false,
};

function root() {
  return $('setup');
}

function stage(state, says, sub) {
  if (!page.avatar) {
    page.avatar = el('jig-avatar', { framing: 'full', shape: 'none', 'data-testid': 'setup-avatar' });
    page.avatar.theme = document.documentElement.dataset.theme;
    document.addEventListener('jig-themechange', () => { page.avatar.theme = document.documentElement.dataset.theme; });
  }
  try {
    page.avatar.setState(state);
  } catch {
    /* the avatar shows its last state */
  }
  return el('section', { class: 'setup-stage', 'aria-live': 'polite' }, page.avatar,
    el('h2', { class: 'setup-says', id: 'setup-says', tabindex: '-1', 'data-testid': 'setup-says', text: says }),
    sub ? el('p', { class: 'setup-sub', 'data-testid': 'setup-sub', text: sub }) : null);
}

function show(...parts) {
  const r = root();
  r.replaceChildren(...parts);
  r.hidden = false;
  $('app').hidden = true;
  $('open-settings').hidden = true;
  const says = $('setup-says');
  if (says) says.focus({ preventScroll: true });
  window.scrollTo({ top: 0 });
}

function close() {
  root().hidden = true;
  root().replaceChildren();
  $('app').hidden = false;
  $('open-settings').hidden = false;
}

function problemBox(problem, detail) {
  return el('div', { class: 'setup-problem', role: 'alert', 'data-testid': 'setup-problem' },
    el('strong', { text: problem.title }),
    el('p', {}, ...linkify(problem.text)),
    detail ? el('details', {}, el('summary', { text: 'Details' }), el('p', { class: 'setup-detail', text: detail })) : null);
}

/** First screen in set-up mode: say what's wrong (if anything) and what to do. */
function welcome() {
  const s = page.status.setup;
  if (!s.configured) {
    chooseScreen();
    return;
  }
  const p = s.problem;
  const actions = [];
  if (p.kind === 'consent' || p.kind === 'key') {
    actions.push(el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'setup-fix-cloud', text: p.kind === 'key' ? 'Add the API key' : 'Review and agree', onclick: () => cloudScreen(page.status.connection.agent.provider) }));
  } else {
    actions.push(el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'setup-retry', text: 'Try again', onclick: () => runCheck('/setup/retry', {}, welcome) }));
  }
  actions.push(el('button', { type: 'button', class: 'btn', 'data-testid': 'setup-choose', text: 'Choose a different model', onclick: chooseScreen }));
  const card = el('div', { class: 'setup-card' }, problemBox(p, s.detail));
  if (s.recheck) card.append(el('p', { class: 'hint', 'data-testid': 'setup-recheck', text: 'I\u2019ll keep trying by myself every few seconds, and start as soon as it answers.' }));
  card.append(el('div', { class: 'setup-actions' }, actions));
  card.append(updatesLink());
  show(stage('idle', 'I can\u2019t start yet.', 'Jig stays off until its model passes its checks. Here\u2019s what happened.'), card);
}

/** While the set-up page is open, notice when the agent starts by itself (its model app came up). */
function watchForStart() {
  setInterval(async () => {
    if (page.mode !== 'setup' || page.checking) return;
    try {
      const s = await call('/status');
      if (s.show_updates) location.hash = '#settings/updates';
      if (s.status !== 'setup') location.reload();
    } catch {
      /* Jig is restarting or unreachable: try again next time */
    }
  }, 5000);
}

function updatesLink() {
  return el('p', { class: 'hint' },
    el('a', { href: '#settings/updates', 'data-testid': 'setup-updates', text: 'About and updates' }));
}

function backButton(onclick) {
  return el('button', { type: 'button', class: 'btn btn-small btn-quiet setup-back', 'data-testid': 'setup-back', text: '\u2190 Back', onclick });
}

function cancelButton() {
  return page.mode === 'change'
    ? el('button', { type: 'button', class: 'btn btn-small btn-quiet setup-back', 'data-testid': 'setup-cancel', text: '\u2190 Back to Settings', onclick: close })
    : null;
}

/* Choosing a model app that's already running. */
async function chooseScreen() {
  const list = el('div', { class: 'pick-list', 'data-testid': 'server-list' }, el('p', { class: 'hint', text: 'Looking for model apps on this computer\u2026' }));
  const useBtn = el('button', { type: 'submit', class: 'btn btn-primary', 'data-testid': 'setup-use',
    text: page.mode === 'change' ? 'Check it and switch' : 'Check it and start', disabled: true });
  const visionBox = el('label', { class: 'switch-row', hidden: true },
    el('input', { type: 'checkbox', id: 'setup-vision', 'data-testid': 'setup-vision' }),
    el('span', { text: 'Let Jig look at pictures. This model can see them; Jig checks that before using it.' }));
  const note = el('p', { class: 'setup-note', 'data-testid': 'context-note', hidden: true });
  const form = el('form', { class: 'setup-card', 'data-testid': 'setup-choose-card' },
    el('h3', { text: 'Model apps on this computer' }), list, note, visionBox, el('div', { class: 'setup-actions' }, useBtn));
  const address = el('input', { type: 'text', id: 'setup-address', placeholder: 'http://127.0.0.1:8000', spellcheck: false, 'data-testid': 'setup-address' });
  const elsewhere = el('details', { class: 'setup-card', 'data-testid': 'setup-elsewhere' },
    el('summary', { text: 'My model app uses a different address' }),
    el('form', { class: 'setup-row', onsubmit: (e) => { e.preventDefault(); look(address.value.trim()); } },
      el('label', { class: 'setup-field', for: 'setup-address' }, 'Address', address),
      el('button', { type: 'submit', class: 'btn', 'data-testid': 'setup-look', text: 'Look' })));
  const help = el('details', { class: 'setup-card', id: 'setup-help', 'data-testid': 'setup-help' },
    el('summary', { text: 'I don\u2019t have a model app yet' }), el('p', { class: 'hint', text: 'Checking your graphics card\u2026' }));
  const cloud = el('div', { class: 'setup-card' },
    el('h3', { text: 'Or use a cloud model' }),
    el('p', { class: 'hint', text: 'A model run by a company such as OpenAI or Anthropic. It needs an API key, they charge you for what you use, and what you ask Jig is sent to them.' }),
    el('div', { class: 'setup-actions' }, el('button', { type: 'button', class: 'btn', 'data-testid': 'setup-cloud', text: 'Use a cloud model instead', onclick: () => cloudScreen() })));
  const intro = page.mode === 'change'
    ? stage('idle', 'Which model should I use?', 'Choose a model and I\u2019ll check it works before switching. If it doesn\u2019t pass, I\u2019ll keep using the one I have.')
    : stage('idle', 'Hi! I\u2019m Jig.', 'I need a model to think with. Choose one and I\u2019ll check it works before I start.');
  show(...[cancelButton(), intro, form, help, elsewhere, cloud, updatesLink()].filter(Boolean));

  let picked = null;
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!picked) return;
    const choice = { kind: 'local', app: picked.server.app, base_url: picked.server.base_url, name: picked.model.id, vision: $('setup-vision').checked };
    runCheck('/setup/apply', choice, chooseScreen);
  });

  function renderServers(servers, installed = []) {
    list.replaceChildren();
    picked = null;
    useBtn.disabled = true;
    note.hidden = true;
    visionBox.hidden = true;
    const off = installed.filter((a) => !a.running);
    for (const app of off) {
      const start = app.can_start ? el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': `setup-start-${app.app}`, text: 'Start it for me', onclick: () => startApp(app, start) }) : null;
      list.append(el('div', { class: 'setup-note', 'data-testid': `installed-${app.app}` }, el('p', { text: app.advice }),
        el('div', { class: 'setup-actions' }, start, el('button', { type: 'button', class: 'btn', text: 'Look again', onclick: () => look('') }))));
    }
    if (!servers.length) {
      if (!off.length) {
        list.append(el('p', { class: 'hint', 'data-testid': 'no-servers', text: 'I didn\u2019t find a model app running. Start yours, or see below for how to get one, then choose Look again.' }),
          el('div', { class: 'setup-actions' }, el('button', { type: 'button', class: 'btn', 'data-testid': 'setup-look-again', text: 'Look again', onclick: () => look('') })));
        help.open = true;
      }
      return;
    }
    for (const server of servers) {
      list.append(el('p', { class: 'hint-quiet', text: `${server.label}, at ${server.base_url}` }));
      if (!server.models.length) {
        list.append(el('p', { class: 'hint', 'data-testid': `no-models-${server.app}`, text: server.advice || `${server.label} is running but has no models yet.` }),
          el('div', { class: 'setup-actions' }, el('button', { type: 'button', class: 'btn', text: 'Look again', onclick: () => look('') })));
        help.open = true;
        page.helpApp = server.app;
      }
      for (const model of server.models) {
        const tags = [];
        const inUse = page.mode === 'change' && page.status && page.status.model
          && page.status.model.base_url === server.base_url && page.status.model.model === model.id;
        if (inUse) tags.push(el('span', { class: 'pick-tag', 'data-testid': 'model-in-use', text: 'In use now' }));
        if (model.loaded === true) tags.push(el('span', { class: 'pick-tag', text: 'Loaded' }));
        if (model.loaded === false) tags.push(el('span', { class: 'pick-tag warn', text: 'Not loaded' }));
        if (model.tools === false) tags.push(el('span', { class: 'pick-tag warn', 'data-testid': 'no-tools', text: 'Can\u2019t use tools' }));
        if (model.context) {
          tags.push(el('span', { class: `pick-tag${model.context < ADVISED_CONTEXT ? ' warn' : ''}`, text: `${kTokens(model.context)} context` }));
        }
        const input = el('input', { type: 'radio', name: 'setup-model', value: `${server.base_url}|${model.id}` });
        input.addEventListener('change', () => {
          picked = { server, model };
          useBtn.disabled = false;
          visionBox.hidden = model.vision !== true;
          if (model.vision !== true) $('setup-vision').checked = false;
          const advice = contextAdvice(server, model);
          note.hidden = !advice;
          note.textContent = advice || '';
        });
        list.append(el('label', { class: 'pick', 'data-testid': 'server-model' }, input,
          el('span', {}, el('strong', {}, model.id, ...tags), el('span', { class: 'hint-quiet', text: server.label }))));
      }
    }
  }

  async function look(url) {
    list.replaceChildren(el('p', { class: 'hint', text: 'Looking\u2026' }));
    try {
      const found = await call(`/setup/discover${url ? `?url=${encodeURIComponent(url)}` : ''}`);
      page.servers = found.servers;
      page.installedApps = (found.installed || []).map((a) => a.app).concat(found.servers.map((s) => s.app));
      page.helpApp = (found.installed || []).map((a) => a.app)[0] || page.helpApp;
      renderServers(found.servers, found.installed || []);
      help.querySelector(`[data-app="${page.helpApp}"]`)?.click();
      if (url && !found.servers.some((s) => s.base_url.startsWith(url.replace(/\/+$/, '').replace(/\/v1$/, '')))) {
        list.prepend(el('p', { class: 'setup-note', text: `Nothing answered like a model server at ${url}. Check the address and that the app\u2019s server is on.` }));
      }
    } catch (err) {
      list.replaceChildren(el('p', { class: 'setup-note', text: `I couldn\u2019t look for model apps: ${err.message}` }));
    }
  }

  async function startApp(app, button) {
    button.disabled = true;
    button.textContent = `Starting ${app.label}\u2026`;
    try {
      const found = await call('/setup/start-app', { method: 'POST', body: { app: app.app } });
      page.servers = found.servers;
      renderServers(found.servers, found.installed || []);
    } catch (err) {
      button.disabled = false;
      button.textContent = 'Start it for me';
      button.after(el('span', { class: 'hint warn-text', text: ` ${err.message}` }));
    }
  }

  look('');
  renderHelp(help, () => look(''));
}

function contextAdvice(server, model) {
  if (model.tools === false) return `${server.label} says this model can\u2019t use tools, and Jig needs them to do anything for you. Choose one that can (on ollama.com, models that can are marked \u201ctools\u201d).`;
  if (model.loaded === false && server.app === 'lmstudio') return 'LM Studio hasn\u2019t loaded this model yet. When you choose it, Jig loads it with a 32K context.';
  if (!model.context || model.context >= ADVISED_CONTEXT) return '';
  const fix = {
    ollama: 'In the Ollama app, open Settings and set Context length to 32k, then choose Look again.',
    lmstudio: 'In LM Studio, reload the model with Context Length set to 32768.',
    llamacpp: 'Restart llama-server with -c 32768.',
  }[server.app] || 'Raise the context length in your model app.';
  return `This model has a ${kTokens(model.context)} context. Jig works best with 32K or more, or it may lose track of longer tasks. ${fix}`;
}

/* "I don't have a model app yet": the graphics card, and the measured suggestion for it. */
async function renderHelp(box, lookAgain) {
  let info;
  try {
    info = page.gpu || await call('/setup/gpu');
    page.gpu = info;
  } catch (err) {
    box.replaceChildren(box.firstElementChild, el('p', { class: 'setup-note', text: `I couldn\u2019t check your graphics card: ${err.message}` }));
    return;
  }
  const gpu = info.gpu;
  const rec = info.recommendation;
  const parts = [box.firstElementChild];
  parts.push(el('p', { 'data-testid': 'gpu-line', text: gpu.found ? `Your graphics card: ${gpu.name}, with ${Math.round(gpu.total_gb)} GB of memory.` : 'I couldn\u2019t find a graphics card I can measure.' }));
  parts.push(el('p', { class: 'hint', 'data-testid': 'recommendation', text: rec.text }));
  if (rec.get_it) {
    const body = el('div', { 'data-testid': 'get-it' });
    const tabs = el('div', { class: 'app-tabs', role: 'group', 'aria-label': 'Model app' });
    const APPS = [['lmstudio', 'LM Studio'], ['ollama', 'Ollama'], ['llamacpp', 'llama.cpp']];
    const showApp = (app) => {
      for (const b of tabs.children) b.setAttribute('aria-pressed', String(b.dataset.app === app));
      const g = rec.get_it[app];
      const rows = [];
      if (app !== 'llamacpp' && (page.installedApps || []).includes(app)) {
        rows.push(el('p', { class: 'hint', text: `You already have ${app === 'ollama' ? 'Ollama' : 'LM Studio'}, so:` }));
        rows.push(el('ol', { class: 'steps' }, g.steps.map((t) => el('li', { text: t }))));
      } else if (app === 'lmstudio') {
        rows.push(el('p', { class: 'hint' }, 'The easiest to start with. Get it from ', el('a', { href: 'https://lmstudio.ai', target: '_blank', rel: 'noopener', text: 'lmstudio.ai' }), ', then:'));
        rows.push(el('ol', { class: 'steps' }, g.steps.map((t) => el('li', { text: t }))));
      } else if (app === 'ollama') {
        rows.push(el('p', { class: 'hint' }, 'Get it from ', el('a', { href: 'https://ollama.com/download', target: '_blank', rel: 'noopener', text: 'ollama.com' }), ', then:'));
        rows.push(el('ol', { class: 'steps' }, g.steps.map((t) => el('li', { text: t }))));
      } else {
        rows.push(el('p', { class: 'hint' }, 'For people happy with a terminal. Download ', el('a', { href: g.download, target: '_blank', rel: 'noopener', text: g.file }), ' and llama.cpp\u2019s llama-server, then run:'));
        rows.push(el('p', { class: 'cmd', text: g.command }));
      }
      body.replaceChildren(...rows);
    };
    for (const [app, label] of APPS) {
      tabs.append(el('button', { type: 'button', class: 'btn btn-small', 'data-app': app, 'aria-pressed': 'false', text: label, onclick: () => showApp(app) }));
    }
    parts.push(tabs, body);
    showApp(page.helpApp && rec.get_it[page.helpApp] ? page.helpApp : 'lmstudio');
  }
  parts.push(el('div', { class: 'setup-actions' }, el('button', { type: 'button', class: 'btn', text: 'I\u2019ve started it: look again', onclick: lookAgain })));
  box.replaceChildren(...parts);
}

/* A cloud model: provider, API key (into the vault), and explicit consent with the full disclosure. */
async function cloudScreen(preselect) {
  const providersBox = el('fieldset', { class: 'pick-list', 'data-testid': 'provider-list' }, el('legend', { class: 'sr-only', text: 'Provider' }), el('p', { class: 'hint', text: 'Loading\u2026' }));
  const keyInput = el('input', { type: 'password', id: 'setup-key', autocomplete: 'off', spellcheck: false, 'data-testid': 'setup-key' });
  const keyState = el('p', { class: 'hint-quiet', 'data-testid': 'key-state' });
  const keyLink = el('p', { class: 'hint' });
  const saveKey = el('button', { type: 'submit', class: 'btn', 'data-testid': 'setup-save-key', text: 'Save key' });
  const disclosure = el('pre', { class: 'disclosure', 'data-testid': 'setup-disclosure', tabindex: '0' });
  const agree = el('input', { type: 'checkbox', id: 'setup-agree', 'data-testid': 'setup-agree' });
  const agreeRow = el('label', { class: 'switch-row' }, agree, el('span', { id: 'setup-agree-text' }));
  const startBtn = el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'setup-cloud-start', text: 'Check it and start', disabled: true });
  const keyCard = el('form', { class: 'setup-card' },
    el('h3', { text: 'Your API key' }), keyLink,
    el('div', { class: 'setup-row' }, el('label', { class: 'setup-field', for: 'setup-key' }, 'API key', keyInput), saveKey),
    keyState,
    el('p', { class: 'hint-quiet', text: 'The key goes straight into Jig\u2019s vault on this computer. It\u2019s never shown again, and no tool can use it.' }));
  const givenNote = el('p', { class: 'hint', hidden: true, text: 'You\u2019ve already agreed to this. You can withdraw it in Settings at any time.' });
  const consentCard = el('div', { class: 'setup-card', 'data-testid': 'setup-consent' },
    el('h3', { text: 'What will be sent' }), disclosure, givenNote, agreeRow, el('div', { class: 'setup-actions' }, startBtn));
  keyCard.hidden = true; // until a provider is chosen
  consentCard.hidden = true;
  show(...[backButton(page.status && page.status.status === 'setup' && page.status.setup.configured && page.mode === 'setup' ? welcome : chooseScreen),
    stage('idle', 'Use a cloud model', 'You\u2019ll need an account and an API key with the provider. They charge you for what you use.'),
    el('div', { class: 'setup-card' }, el('h3', { text: 'Provider' }), providersBox), keyCard, consentCard]);

  let provider = null;
  let alreadyGiven = false;
  const ready = () => {
    startBtn.disabled = !(provider && provider.key_stored && (alreadyGiven || agree.checked));
  };
  agree.addEventListener('change', ready);

  async function choose(p) {
    provider = p;
    keyCard.hidden = false;
    consentCard.hidden = false;
    keyLink.replaceChildren('Get a key from ', el('a', { href: p.key_url, target: '_blank', rel: 'noopener', text: p.key_url }),
      `, copy it, and paste it here. ${p.label} will ask you to add a payment method.`);
    keyState.textContent = p.key_stored ? `A key for ${p.label} is saved. Paste a new one to replace it.` : 'No key saved yet.';
    disclosure.textContent = 'Loading\u2026';
    agree.checked = false;
    try {
      const d = await call('/setup/cloud/disclosure', { method: 'POST', body: { kind: 'cloud', provider: p.id } });
      disclosure.textContent = d.text;
      alreadyGiven = d.already_given;
      agreeRow.hidden = alreadyGiven;
      $('setup-agree-text').textContent = `I agree: send what I ask Jig, and what it works with, to ${p.label}.`;
      givenNote.hidden = !alreadyGiven;
    } catch (err) {
      disclosure.textContent = `I couldn\u2019t load what would be sent: ${err.message}`;
    }
    ready();
  }

  keyCard.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!provider) return;
    saveKey.disabled = true;
    try {
      await call('/setup/cloud/key', { method: 'PUT', body: { provider: provider.id, key: keyInput.value } });
      keyInput.value = '';
      provider.key_stored = true;
      keyState.textContent = `Saved. Jig will check ${provider.label} accepts it when you start.`;
    } catch (err) {
      keyState.textContent = err.message;
    } finally {
      saveKey.disabled = false;
      ready();
    }
  });

  startBtn.addEventListener('click', () => {
    runCheck('/setup/apply', { kind: 'cloud', provider: provider.id, consent: alreadyGiven || agree.checked }, () => cloudScreen(provider.id));
  });

  try {
    page.providers = await call('/setup/providers');
  } catch (err) {
    providersBox.replaceChildren(el('p', { class: 'setup-note', text: `I couldn\u2019t load the providers: ${err.message}` }));
    return;
  }
  providersBox.replaceChildren(el('legend', { class: 'sr-only', text: 'Provider' }));
  for (const p of page.providers) {
    const input = el('input', { type: 'radio', name: 'setup-provider', value: p.id, onchange: () => choose(p) });
    providersBox.append(el('label', { class: 'pick', 'data-testid': 'provider' }, input,
      el('span', {}, el('strong', {}, p.label, p.key_stored ? el('span', { class: 'pick-tag', text: 'Key saved' }) : null),
        el('span', { class: 'hint-quiet', text: `Model: ${p.model}` }))));
    if (p.id === preselect) {
      input.checked = true;
      choose(p);
    }
  }
}

/* Run the checks (or a retry) and show them live. */
async function runCheck(path, body, back) {
  page.lastChoice = { path, body, back };
  const items = new Map(CHECK_STEPS.map(([id, text]) => [id, el('li', { 'data-s': 'todo', 'data-step': id, text })]));
  const list = el('ul', { class: 'check-list', 'data-testid': 'check-list' }, [...items.values()]);
  for (const id of ['loading', 'sentinel', 'vision']) items.get(id).hidden = true;
  const card = el('div', { class: 'setup-card', 'aria-live': 'polite' }, list);
  show(stage('thinking', 'Checking\u2026', 'I\u2019m trying the model out for real, the same way I do every time I start.'), card);
  let current = null;
  const mark = (step) => {
    if (!items.has(step)) return;
    if (current && current !== step) items.get(current).dataset.s = 'done';
    current = step;
    items.get(step).hidden = false;
    items.get(step).dataset.s = 'doing';
  };
  let last;
  page.checking = true;
  try {
    last = await stream(path, body, (line) => {
      if (line.step) mark(line.step);
    });
  } catch (err) {
    last = { error: { title: 'That didn\u2019t work.', text: err.message } };
  }
  if (last && last.done) {
    for (const li of items.values()) if (!li.hidden) li.dataset.s = 'done';
    doneScreen(last);
    return;
  }
  page.checking = false; // after a pass it stays set, so the All set! screen isn't reloaded away
  if (current) items.get(current).dataset.s = 'failed';
  const error = (last && last.error) || { title: 'The check stopped.', text: 'Jig didn\u2019t say why. Look in its log for details.' };
  const cloud = body && body.kind === 'cloud';
  card.append(problemBox(error, last && last.detail),
    el('div', { class: 'setup-actions' },
      cloud && error.kind === 'key'
        ? el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'setup-change-key', text: 'Change the key', onclick: back })
        : el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'setup-try-again', text: 'Try again', onclick: () => runCheck(path, body, back) }),
      el('button', { type: 'button', class: 'btn', 'data-testid': 'setup-back-choose', text: 'Choose something else', onclick: cloud ? chooseScreen : back })));
  try {
    page.avatar.setState('error');
  } catch {
    /* keep the last state */
  }
  $('setup-says').textContent = page.mode === 'change' ? 'That one didn\u2019t pass.' : 'Not quite yet.';
  const sub = root().querySelector('.setup-sub');
  if (sub) sub.textContent = page.mode === 'change' ? 'Nothing has changed: I\u2019m still using the model I had.' : 'Jig stays off until a model passes. Here\u2019s what happened.';
}

function doneScreen(result) {
  const model = result.model && result.model.model;
  show(stage('success', 'All set!', model ? `I\u2019m using ${model}, and it passed every check.` : 'The model passed every check.'),
    el('div', { class: 'setup-card' },
      ...(result.notes || []).map((t) => el('p', { class: 'setup-note', 'data-testid': 'setup-done-note', text: t })),
      el('p', { text: 'You can change the model, add a cloud key, or let Jig run code from Settings at any time.' }),
      el('div', { class: 'setup-actions' },
        el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'setup-start-chatting', text: 'Start chatting', onclick: () => { location.hash = ''; location.reload(); } }))));
}

/** Called by app.js once signed in. Shows the set-up page and returns true if Jig is in set-up mode. */
export async function showSetupIfNeeded() {
  let status;
  try {
    status = await call('/status');
  } catch {
    return false; // app.js reports the problem
  }
  if (status.status !== 'setup') return false;
  page.mode = 'setup';
  page.status = status;
  welcome();
  watchForStart();
  return true;
}

/* ---------- Settings: change model, cloud keys and consent ---------- */

async function renderModelSettings() {
  const box = $('model-change');
  if (!box) return;
  let state;
  try {
    state = await call('/setup');
  } catch (err) {
    if (err.status === 404) return; // an older Jig without set-up mode
    box.replaceChildren(el('p', { class: 'hint warn-text', text: `Jig couldn\u2019t load the model settings: ${err.message}` }));
    return;
  }
  const parts = [el('div', { class: 'setup-actions' },
    el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'change-model', text: 'Change model\u2026', onclick: openChange }))];
  let providers = [];
  try {
    providers = await call('/setup/providers');
  } catch {
    /* shown below as no keys */
  }
  const saved = providers.filter((p) => p.key_stored);
  if (saved.length) {
    parts.push(el('h4', { text: 'Cloud API keys' }), el('div', { class: 'key-list', 'data-testid': 'key-list' }, saved.map((p) => el('div', { class: 'key-row' },
      el('span', { text: `${p.label}: a key is saved in the vault.` }),
      el('button', { type: 'button', class: 'btn btn-small', 'data-testid': `remove-key-${p.id}`, text: 'Remove', onclick: (e) => removeKey(e.target, p) })))));
  }
  if (state.model.location.kind === 'cloud') {
    parts.push(el('div', { class: 'setup-actions' }, el('button', { type: 'button', class: 'btn btn-danger', 'data-testid': 'withdraw-consent', text: 'Withdraw my OK for the cloud model\u2026', onclick: (e) => withdraw(e.target) })));
  }
  box.replaceChildren(el('div', { class: 'settings-model-actions' }, parts));
}

async function removeKey(button, p) {
  const ok = await confirmBox({ title: `Remove the ${p.label} key?`, body: ['Jig deletes it from its vault on this computer. You can add it again later.'], ok: 'Remove', danger: true });
  if (!ok) return;
  button.disabled = true;
  try {
    await call(`/setup/cloud/key/${p.id}`, { method: 'DELETE' });
    renderModelSettings();
  } catch (err) {
    button.disabled = false;
    button.after(el('span', { class: 'hint warn-text', text: ` ${err.message}` }));
  }
}

async function withdraw(button) {
  const ok = await confirmBox({ title: 'Withdraw your OK for the cloud model?', body: ['Jig stops using the cloud model straight away and turns its agent off, until you choose another model or agree again.'], ok: 'Withdraw', danger: true });
  if (!ok) return;
  button.disabled = true;
  try {
    await call('/setup/cloud/revoke', { method: 'POST' });
    location.hash = '';
    location.reload();
  } catch (err) {
    button.disabled = false;
    button.after(el('span', { class: 'hint warn-text', text: ` ${err.message}` }));
  }
}

async function openChange() {
  page.mode = 'change';
  try {
    page.status = await call('/status');
  } catch {
    page.status = null;
  }
  chooseScreen();
}

/* ---------- Settings: running code (Docker) ---------- */

async function renderCode() {
  const box = $('code-actions');
  if (!box) return;
  let s;
  try {
    s = await call('/setup/sandbox');
  } catch (err) {
    if (err.status !== 404) box.replaceChildren(el('p', { class: 'hint warn-text', text: `Jig couldn\u2019t check Docker: ${err.message}` }));
    return;
  }
  // These steps replace the terminal steps app.js shows from /sandbox.
  const steps = $('code-steps');
  if (steps) {
    steps.dataset.fromSetup = '1';
    steps.replaceChildren(...s.steps.map((t) => el('li', {}, ...linkify(t))));
  }
  const parts = [];
  if (s.on) {
    parts.push(el('button', { type: 'button', class: 'btn', 'data-testid': 'code-off', text: 'Turn off running code\u2026', onclick: (e) => setCode(e.target, false) }));
  } else if (s.can_turn_on) {
    parts.push(el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'code-on', text: 'Turn on running code\u2026', onclick: (e) => setCode(e.target, true) }));
  } else if (s.can_build) {
    parts.push(el('button', { type: 'button', class: 'btn btn-primary', 'data-testid': 'code-build', text: 'Build the safe container', onclick: (e) => build(e.target) }));
  } else if (!s.docker || !s.docker.daemon) {
    parts.push(el('button', { type: 'button', class: 'btn', 'data-testid': 'code-recheck', text: 'Check for Docker again', onclick: renderCode }));
  }
  parts.push(el('p', { class: 'hint-quiet', id: 'code-progress', 'aria-live': 'polite', 'data-testid': 'code-progress' }));
  box.replaceChildren(...parts);
  if (!s.docker && !s.on) return;
  if (s.docker && !s.docker.cli) {
    box.append(el('p', { class: 'hint', text: 'Docker is a free app that runs programs in sealed-off containers. Installing it needs administrator rights on this computer, and a restart.' }));
  }
}

export function linkify(text) {
  const parts = [];
  let rest = text;
  const re = /https?:\/\/[^\s)]+[^\s).,]/;
  let m;
  while ((m = rest.match(re))) {
    parts.push(rest.slice(0, m.index), el('a', { href: m[0], target: '_blank', rel: 'noopener', text: m[0] }));
    rest = rest.slice(m.index + m[0].length);
  }
  parts.push(rest);
  return parts;
}

async function build(button) {
  button.disabled = true;
  const progress = $('code-progress');
  progress.textContent = 'Building the safe container. The first time downloads about a gigabyte, so it can take a while\u2026';
  try {
    const last = await stream('/setup/sandbox/build', {}, () => {});
    if (last && last.done) {
      progress.textContent = 'Built.';
      renderCode();
      return;
    }
    progress.textContent = `${last.error.title} ${last.error.text}`;
  } catch (err) {
    progress.textContent = err.message;
  }
  button.disabled = false;
}

async function setCode(button, enable) {
  const ok = await confirmBox(enable ? {
    title: 'Let Jig run code?',
    body: ['Jig will be able to run code, shell commands and a web browser, only inside a sealed-off Docker container, so nothing it runs can touch the rest of this computer.',
      'Each command or piece of code still asks you first. Jig restarts to switch this on.'],
    ok: 'Turn on',
  } : {
    title: 'Turn off running code?',
    body: ['Jig will stop being able to run code, shell commands or a web browser. It restarts to switch this off.'],
    ok: 'Turn off',
  });
  if (!ok) return;
  button.disabled = true;
  const progress = $('code-progress');
  progress.textContent = enable ? 'Checking and restarting Jig\u2026' : 'Restarting Jig\u2026';
  try {
    const last = await stream('/setup/sandbox', { enable, confirm: true }, (line) => {
      if (line.text) progress.textContent = `${line.text}\u2026`;
    });
    if (last && last.done) {
      location.reload();
      return;
    }
    progress.textContent = `${last.error.title} ${last.error.text}`;
  } catch (err) {
    progress.textContent = err.message;
  }
  button.disabled = false;
}

function settingsVisible() {
  const m = location.hash.match(/^#settings(?:\/([a-z]+))?$/);
  return m && (m[1] || 'model') === 'model';
}

function refreshSettings() {
  if (!settingsVisible() || $('app').hidden) return;
  renderModelSettings();
  renderCode();
}
window.addEventListener('hashchange', refreshSettings);
document.getElementById('status-refresh')?.addEventListener('click', refreshSettings);
new MutationObserver(refreshSettings).observe($('app'), { attributes: true, attributeFilter: ['hidden'] });
