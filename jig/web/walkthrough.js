/*
 * Guided set-up for connecting an account: Settings > Connections > <account> > "Set up step by step"
 * (#settings/connections/<provider>). One step at a time, in Jig's voice, with what the provider's page looks
 * like, what to click, and a real check before moving on. The steps come from GET /connections
 * (jig/connectors/walkthrough.py); the checks are POST /connections/<provider>/walkthrough/<check>.
 *
 * Secrets (tokens, passwords, Google's client file) are only typed into the step's own form, which sends them
 * straight to Jig's vault and clears the field; nothing here keeps them. Only non-secret progress (which step,
 * what a check found) is kept in this tab's sessionStorage, and the Google project ID in localStorage.
 */

let h = null; // helpers from app.js: el, api, act, showError, reload
const view = { provider: null, key: '', avatar: null, node: null };
const PROJECT_KEY = 'jig.google.project';

export function initWalkthrough(helpers) {
  h = helpers;
  if (!document.querySelector('link[href="/web/walkthrough.css"]')) {
    document.head.append(Object.assign(document.createElement('link'), { rel: 'stylesheet', href: '/web/walkthrough.css' }));
  }
}

const stateKey = (provider) => `jig.walk.${provider}`;

function load(provider) {
  try {
    return { index: 0, done: {}, results: {}, showShared: false, ...JSON.parse(sessionStorage.getItem(stateKey(provider)) || '{}') };
  } catch {
    return { index: 0, done: {}, results: {}, showShared: false };
  }
}

function save(provider, s) {
  sessionStorage.setItem(stateKey(provider), JSON.stringify(s));
}

function project() {
  return (localStorage.getItem(PROJECT_KEY) || '').trim();
}

function linkUrl(link, s) {
  let url = link.url;
  const app = s.results.create && s.results.create.application_id;
  if (url.includes('{application_id}')) {
    if (!app) return null;
    url = url.replace('{application_id}', app);
  }
  if (link.project && project()) url += `${url.includes('?') ? '&' : '?'}project=${encodeURIComponent(project())}`;
  return url;
}

function visibleSteps(row, s) {
  return row.walkthrough.filter((step) => !(step.shared && row.client_configured && !s.showShared));
}

function setAvatar(state) {
  if (!view.avatar) return;
  try {
    view.avatar.setState(state);
  } catch {
    /* the avatar keeps its last state */
  }
}

function avatar() {
  if (!view.avatar) {
    view.avatar = h.el('jig-avatar', { framing: 'full', shape: 'none', 'data-testid': 'walkthrough-avatar' });
    view.avatar.theme = document.documentElement.dataset.theme;
    document.addEventListener('jig-themechange', () => { view.avatar.theme = document.documentElement.dataset.theme; });
  }
  return view.avatar;
}

function apiMessage(err) {
  return (err && err.data && typeof err.data.error === 'string') ? err.data.error : (err && err.message) || String(err);
}

async function runCheck(row, step, check, values, button) {
  const s = load(row.provider);
  if (button) button.disabled = true;
  setAvatar('working');
  let out;
  try {
    out = await h.api(`/connections/${encodeURIComponent(row.provider)}/walkthrough/${check}`, { method: 'POST', body: { confirm: true, values } });
  } catch (err) {
    out = { ok: false, say: apiMessage(err), error: true };
  } finally {
    if (button) button.disabled = false;
  }
  const key = check === 'read' ? `${step.id}:read` : step.id;
  s.results[key] = out;
  if (out.ok && check !== 'pick') s.done[step.id] = true;
  if (out.project_id) localStorage.setItem(PROJECT_KEY, out.project_id);
  save(row.provider, s);
  setAvatar(out.ok ? 'success' : 'error');
  render(row, true);
  return out;
}

function result(out, testid) {
  if (!out) return null;
  return h.el('p', { class: `walk-result ${out.ok ? 'ok' : 'not-yet'}`, role: 'status', 'data-testid': testid, text: out.say });
}

function field(input, value) {
  return h.el('label', {}, input.prompt + (input.optional ? ' (optional)' : ''), h.el('input', {
    type: input.secret ? 'password' : 'text', name: input.name, autocomplete: input.secret ? 'new-password' : 'off',
    spellcheck: 'false', autocapitalize: 'off', value: input.secret ? '' : (value || ''), placeholder: input.placeholder || '',
    'data-testid': `walkthrough-input-${input.name}`,
  }));
}

function accessSelect(row) {
  return h.el('label', {}, 'What Jig may do', h.el('select', { 'data-testid': 'walkthrough-access' },
    Object.entries(row.access_levels).map(([k, v]) => h.el('option', { value: k, selected: k === (row.access || row.default_access), text: `${k}: ${v.description}` }))));
}

/* ---------- one control per kind of step ---------- */

function actionNode(row, step, s, steps) {
  const a = step.action;
  const el = h.el;
  if (a.type === 'next') return null;

  if (a.type === 'done') {
    const command = a.command ? a.command.replace('{signal_cli}', (s.results.install && s.results.install.signal_cli) || 'C:\\path\\to\\signal-cli.bat') : null;
    return el('div', { class: 'walk-action' },
      command ? el('div', { class: 'walk-command' }, el('code', { 'data-testid': 'walkthrough-command', text: command }),
        el('button', { type: 'button', class: 'btn btn-small', text: 'Copy', onclick: () => navigator.clipboard.writeText(command) })) : null,
      s.done[step.id]
        ? el('p', { class: 'walk-result ok', role: 'status', text: 'Done. On to the next step.' })
        : el('button', {
          type: 'button', class: 'btn btn-primary', 'data-testid': 'walkthrough-done', text: a.label || 'I\u2019ve done this',
          onclick: () => { s.done[step.id] = true; save(row.provider, s); move(row, 1); },
        }));
  }

  if (a.type === 'project') {
    const input = el('input', { type: 'text', autocomplete: 'off', spellcheck: 'false', placeholder: 'jig-472913', value: project(), 'data-testid': 'walkthrough-project' });
    const ok = el('p', { class: 'walk-result', role: 'status' });
    input.addEventListener('input', () => {
      const v = input.value.trim();
      const good = !v || /^[a-z][a-z0-9-]{4,62}$/.test(v);
      ok.textContent = !v ? '' : good ? 'Got it: my links will open in that project.' : 'A project ID is lower-case letters, numbers and dashes, like jig-472913.';
      ok.className = `walk-result ${good ? 'ok' : 'not-yet'}`;
      if (good) {
        if (v) localStorage.setItem(PROJECT_KEY, v); else localStorage.removeItem(PROJECT_KEY);
        s.done[step.id] = true;
        save(row.provider, s);
      }
    });
    return el('div', { class: 'walk-action' }, el('label', {}, 'Project ID (optional)', input), ok);
  }

  if (a.type === 'google_client') {
    const file = el('input', { type: 'file', accept: '.json,application/json', 'data-testid': 'walkthrough-client-file' });
    const upload = el('button', {
      type: 'button', class: 'btn btn-primary', text: 'Use this file', 'data-testid': 'walkthrough-client-upload',
      onclick: async () => {
        const f = file.files && file.files[0];
        if (!f) { h.showError('Choose the file you downloaded from Google first.'); return; }
        if (f.size > 20000) { h.showError('That file is too big to be the client file Google downloads.'); return; }
        const text = await f.text();
        file.value = '';
        upload.disabled = true;
        try {
          await h.api('/connections/google/client', { method: 'POST', body: { confirm: true, client_json: text } });
        } catch (err) {
          const r = load(row.provider);
          r.results[step.id] = { ok: false, say: apiMessage(err) };
          save(row.provider, r);
          setAvatar('error');
          upload.disabled = false;
          render(row, true);
          return;
        }
        await runCheck(row, step, 'google_client', null, upload);
        await h.reload();
      },
    });
    const out = s.results[step.id];
    if (!out && row.client_configured) queueMicrotask(() => runCheck(row, step, 'google_client', null, null));
    return el('div', { class: 'walk-action' },
      row.client_configured ? el('p', { class: 'hint-quiet', text: 'I already have a Google app file. To use a different one, choose it here.' }) : null,
      el('div', { class: 'row wrap' }, el('label', {}, 'The file you downloaded', file), upload),
      result(out, 'walkthrough-result'));
  }

  if (a.type === 'connect') {
    if (!row.client_configured) {
      return el('div', { class: 'walk-action' }, el('p', { class: 'walk-result not-yet', text: row.client_problem || 'The app this signs in with isn\u2019t set up yet.' }));
    }
    const at = row.attempt;
    if (row.connected) {
      s.done[step.id] = true;
      save(row.provider, s);
      return el('div', { class: 'walk-action' }, el('p', { class: 'walk-result ok', role: 'status', 'data-testid': 'walkthrough-connected', text: `Connected as ${row.account}, with \u2018${row.access}\u2019 access.` }));
    }
    const parts = [];
    if (at && at.status === 'waiting' && at.user_code) {
      parts.push(el('div', { class: 'walk-code', 'data-testid': 'walkthrough-code' },
        el('p', { text: 'Type this code on GitHub\u2019s page:' }),
        el('p', { class: 'walk-code-big', 'data-testid': 'walkthrough-user-code', text: at.user_code }),
        el('div', { class: 'row wrap' },
          el('button', { type: 'button', class: 'btn btn-primary', text: 'Copy code', 'data-testid': 'walkthrough-copy-code', onclick: (e) => { navigator.clipboard.writeText(at.user_code); e.currentTarget.textContent = 'Copied'; } }),
          el('a', { class: 'btn', href: at.verification_uri, target: '_blank', rel: 'noopener noreferrer', text: 'Open GitHub\u2019s page \u2197' })),
        el('p', { class: 'hint-quiet', text: 'Waiting for you to choose Authorize on GitHub\u2026 This updates by itself.' })));
    } else if (at && at.status === 'waiting') {
      parts.push(el('p', { class: 'hint-quiet', role: 'status', text: row.kind === 'device' ? 'Getting a code from GitHub\u2026' : 'Waiting for you to finish signing in, in the tab that opened\u2026 This updates by itself.' }));
    } else {
      if (at && at.status === 'failed') parts.push(el('p', { class: 'walk-result not-yet', role: 'status', 'data-testid': 'walkthrough-result', text: `That didn\u2019t work: ${at.error}` }));
      const access = accessSelect(row);
      parts.push(el('div', { class: 'row wrap' }, access, el('button', {
        type: 'button', class: 'btn btn-primary', 'data-testid': 'walkthrough-connect', text: at && at.status === 'failed' ? 'Try again' : 'Connect',
        onclick: async (e) => {
          const button = e.currentTarget;
          const out = await h.act(button, () => h.api(`/connections/${encodeURIComponent(row.provider)}/connect`, { method: 'POST', body: { confirm: true, access: access.querySelector('select').value } }));
          if (!out) return;
          setAvatar('thinking');
          window.open(out.auth_url || out.verification_uri, '_blank', 'noopener');
          await h.reload();
        },
      })));
    }
    return el('div', { class: 'walk-action' }, parts);
  }

  if (a.type === 'token') {
    if (row.connected && s.done[step.id]) {
      return el('div', { class: 'walk-action' }, el('p', { class: 'walk-result ok', role: 'status', 'data-testid': 'walkthrough-connected', text: `Connected as ${row.account}, with \u2018${row.access}\u2019 access.` }));
    }
    const known = { ...(s.results.server || {}), ...(s.results.install || {}) };
    const access = accessSelect(row);
    const form = el('form', { class: 'form walk-action', autocomplete: 'off', 'data-testid': 'walkthrough-token-form' },
      el('div', { class: 'row wrap' }, row.inputs.map((i) => field(i, known[i.name]))),
      el('div', { class: 'row wrap' }, access, el('button', { type: 'submit', class: 'btn btn-primary', 'data-testid': 'walkthrough-token-connect', text: row.connected ? 'Connect again' : 'Connect' })),
      el('p', { class: 'hint-quiet', text: 'This goes straight into Jig\u2019s vault on this computer, and is never shown again.' }),
      row.connected ? el('p', { class: 'walk-result ok', text: `Already connected as ${row.account}. You can go on, or connect again.` }) : null,
      result(s.results[step.id], 'walkthrough-result'));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const values = {};
      for (const i of row.inputs) values[i.name] = form.elements[i.name].value;
      for (const i of row.inputs) if (i.secret) form.elements[i.name].value = '';
      const button = form.querySelector('button[type=submit]');
      button.disabled = true;
      setAvatar('working');
      const r = load(row.provider);
      try {
        const out = await h.api(`/connections/${encodeURIComponent(row.provider)}/connect`, { method: 'POST', body: { confirm: true, access: access.querySelector('select').value, method: 'token', values } });
        r.results[step.id] = { ok: true, say: `That works: connected as ${out.account}.` };
        r.done[step.id] = true;
        setAvatar('success');
      } catch (err) {
        r.results[step.id] = { ok: false, say: apiMessage(err) };
        setAvatar('error');
      }
      save(row.provider, r);
      button.disabled = false;
      await h.reload();
      render(row, true);
    });
    if (row.connected && !s.done[step.id]) {
      s.done[step.id] = true;
      save(row.provider, s);
    }
    return form;
  }

  if (a.type === 'check') {
    const input = el('input', { type: 'text', autocomplete: 'off', spellcheck: 'false', placeholder: a.input.placeholder || '', value: (s.results[step.id] && s.results[step.id][a.input.name]) || '', 'data-testid': `walkthrough-input-${a.input.name}` });
    const button = el('button', { type: 'submit', class: 'btn btn-primary', 'data-testid': 'walkthrough-check', text: 'Check' });
    const form = el('form', { class: 'form walk-action', autocomplete: 'off' },
      el('div', { class: 'row wrap' }, el('label', {}, a.input.prompt, input), button),
      result(s.results[step.id], 'walkthrough-result'));
    form.addEventListener('submit', (e) => { e.preventDefault(); runCheck(row, step, a.check, { [a.input.name]: input.value }, button); });
    return form;
  }

  if (a.type === 'pick') {
    if (!row.connected) return el('div', { class: 'walk-action' }, el('p', { class: 'walk-result not-yet', text: 'Connect first (the step before), then come back here.' }));
    const listed = s.results[step.id];
    const read = s.results[`${step.id}:read`];
    if (!listed) queueMicrotask(() => runCheck(row, step, 'pick', null, null));
    const items = (listed && listed.items) || [];
    const again = el('button', { type: 'button', class: 'btn btn-small', 'data-testid': 'walkthrough-look-again', text: 'Look again', onclick: (e) => runCheck(row, step, 'pick', null, e.currentTarget) });
    return el('div', { class: 'walk-action' },
      listed ? result(listed, 'walkthrough-pick-result') : el('p', { class: 'hint-quiet', role: 'status', text: 'Looking\u2026' }),
      items.length ? el('ul', { class: 'walk-pick', 'data-testid': 'walkthrough-pick' }, items.map((i) => el('li', {},
        el('button', {
          type: 'button', class: `btn btn-small${read && read.target === i.id ? ' btn-primary' : ''}`, disabled: i.disabled, 'data-testid': 'walkthrough-pick-item', dataset: { id: i.id },
          text: i.label, onclick: (e) => runCheck(row, step, 'read', { target: i.id }, e.currentTarget),
        }),
        i.note ? el('span', { class: 'hint-quiet', text: ` ${i.note}` }) : null))) : null,
      again,
      result(read, 'walkthrough-result'),
      read && read.ok ? el('details', { class: 'advanced' }, el('summary', { text: 'Its ID, for Jig\u2019s settings file' }),
        el('div', { class: 'walk-command' }, el('code', { text: read.target }), el('button', { type: 'button', class: 'btn btn-small', text: 'Copy', onclick: () => navigator.clipboard.writeText(read.target) })),
        el('p', { class: 'hint-quiet', text: 'Only needed to limit Jig to this one place ([connectors] allowed_targets in jig.toml).' })) : null);
  }

  if (a.type === 'try') {
    if (!row.connected) return el('div', { class: 'walk-action' }, el('p', { class: 'walk-result not-yet', text: 'Connect first (the step before), then come back here.' }));
    const out = s.results[step.id];
    if (!out) queueMicrotask(() => runCheck(row, step, 'try', null, null));
    return el('div', { class: 'walk-action' },
      out ? result(out, 'walkthrough-result') : el('p', { class: 'hint-quiet', role: 'status', text: 'Checking\u2026' }),
      out && !out.ok ? el('button', { type: 'button', class: 'btn btn-small', text: 'Check again', onclick: (e) => runCheck(row, step, 'try', null, e.currentTarget) }) : null);
  }
  return null;
}

function stepDone(row, step, s) {
  const t = step.action.type;
  if (t === 'next' || t === 'project') return true;
  if (t === 'connect') return row.connected;
  if (t === 'token') return row.connected;
  if (t === 'pick') return row.connected && Boolean(s.results[`${step.id}:read`] && s.results[`${step.id}:read`].ok);
  if (t === 'try') return row.connected && Boolean(s.results[step.id] && s.results[step.id].ok);
  return Boolean(s.done[step.id]);
}

function move(row, by) {
  const s = load(row.provider);
  const steps = visibleSteps(row, s);
  s.index = Math.max(0, Math.min(steps.length - 1, s.index + by));
  save(row.provider, s);
  setAvatar('talking');
  render(row, true);
  const says = document.querySelector('[data-testid="walkthrough-says"]');
  if (says) says.focus({ preventScroll: false });
}

/** The walkthrough for one account, or null if this row has none. Re-renders only when something changed. */
export function render(row, force = false) {
  const el = h.el;
  const s = load(row.provider);
  if (!row.client_configured && !s.showShared && row.walkthrough.some((x) => x.shared)) {
    s.showShared = true; // keeps the Google app steps in place once the file is uploaded part-way through
    save(row.provider, s);
  }
  const steps = visibleSteps(row, s);
  if (s.index >= steps.length) s.index = steps.length - 1;
  const step = steps[s.index];
  const key = JSON.stringify([row, s]);
  if (!force && view.node && view.provider === row.provider && view.key === key) return view.node;
  view.provider = row.provider;
  view.key = key;
  const skipped = row.walkthrough.length - steps.length;
  const links = step.links.map((l) => {
    const url = linkUrl(l, s);
    if (!url) return el('span', { class: 'hint-quiet', text: `${l.label}: check your Application ID in the first step, and I'll make this link.` });
    return el('a', { class: 'btn btn-small', href: url, target: '_blank', rel: 'noopener noreferrer', 'data-testid': 'walkthrough-link', text: `${l.label} \u2197` });
  });
  const done = stepDone(row, step, s);
  const last = s.index === steps.length - 1;
  const progress = el('div', {
    class: 'walk-progress', role: 'progressbar', 'aria-valuemin': '1', 'aria-valuemax': String(steps.length), 'aria-valuenow': String(s.index + 1),
    'aria-label': `Step ${s.index + 1} of ${steps.length}`,
  }, el('span'));
  progress.firstChild.style.width = `${Math.round(((s.index + (done ? 1 : 0)) / steps.length) * 100)}%`;

  const node = el('section', { class: 'walk', 'data-testid': 'walkthrough', dataset: { provider: row.provider, step: step.id } },
    el('div', { class: 'walk-top' },
      el('a', { href: '#settings/connections', 'data-testid': 'walkthrough-close', text: '\u2190 All accounts' }),
      el('span', { class: 'walk-count', 'data-testid': 'walkthrough-count', text: `${row.label} \u00b7 step ${s.index + 1} of ${steps.length}` }),
      s.index > 0 ? el('button', { type: 'button', class: 'btn-link', 'data-testid': 'walkthrough-restart', text: 'Start again', onclick: () => { restart(row.provider); render(row, true); } }) : null),
    progress,
    el('div', { class: 'walk-stage' }, avatar(),
      el('div', { class: 'walk-words' },
        el('h4', { class: 'walk-says', tabindex: '-1', 'data-testid': 'walkthrough-says', text: step.title }),
        el('p', { class: 'walk-say', 'data-testid': 'walkthrough-say', text: step.say }))),
    skipped && s.index === 0 ? el('p', { class: 'walk-skip' }, 'Your Google app is already set up, so we can go straight to signing in. ',
      el('button', { type: 'button', class: 'btn-link', text: 'Show the Google app steps', onclick: () => { s.showShared = true; s.index = 0; save(row.provider, s); render(row, true); } })) : null,
    step.see ? el('div', { class: 'walk-see', 'data-testid': 'walkthrough-see' }, el('strong', { text: 'What you\u2019ll see' }), el('p', { text: step.see })) : null,
    step.do.length ? el('div', { class: 'walk-do' }, el('strong', { text: 'What to do' }), el('ol', {}, step.do.map((d) => el('li', { text: d })))) : null,
    links.length ? el('div', { class: 'walk-links' }, links) : null,
    actionNode(row, step, s, steps),
    step.note ? el('p', { class: 'hint-quiet walk-note', text: step.note }) : null,
    el('div', { class: 'walk-nav' },
      s.index > 0 ? el('button', { type: 'button', class: 'btn', 'data-testid': 'walkthrough-back', text: 'Back', onclick: () => move(row, -1) }) : el('span'),
      last
        ? (done ? el('a', { class: 'btn btn-primary', href: '#settings/connections', 'data-testid': 'walkthrough-finish', text: 'Finish' }) : el('span'))
        : el('button', {
          type: 'button', class: 'btn btn-primary', 'data-testid': 'walkthrough-next', disabled: !done, text: 'Next',
          title: done ? '' : 'Finish this step first',
          onclick: () => move(row, 1),
        })));
  if (view.node && view.node !== node && view.node.isConnected) view.node.replaceWith(node);
  view.node = node;
  return node;
}

/** Start again from the first step (the "Set up step by step" button). */
export function restart(provider) {
  const s = load(provider);
  save(provider, { ...s, index: 0, results: {}, done: {} });
  view.key = '';
}
