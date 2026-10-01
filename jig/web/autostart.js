// Autostart row in the Status card: shows whether Jig starts at logon and lets the user turn it on or off.
// Enabling always shows the full disclosure (what is registered, the trigger, the account and the log)
// and needs an explicit OK; the request then carries "confirm": true. Kept separate from app.js.

const cell = () => document.getElementById('st-autostart');

async function call(path, method = 'GET', body) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}

function disclosure(plan) {
  return [
    `Start Jig automatically? This registers (${plan.backend}):`,
    '',
    `Entry: ${plan.entry}`,
    `Command line: ${plan.command_line}`,
    `Trigger: ${plan.trigger}`,
    `Runs as: ${plan.account}`,
    `Logs: ${plan.log_path}`,
    '',
    ...plan.settings.map((s) => `- ${s}`),
    ...(plan.tested ? [] : ['', 'WARNING: this backend has not been tested on a real machine yet.']),
    '',
    'You can turn it off again here or with "jig autostart disable".',
  ].join('\n');
}

function render(info, error) {
  const dd = cell();
  if (!dd) return;
  dd.replaceChildren();
  if (error) {
    dd.textContent = `Error: ${error}`;
    return;
  }
  if (info.applicable === false) {
    // Container mode: Docker restarts Jig, so there is nothing to register here.
    dd.textContent = info.hint;
    dd.title = info.reason;
    return;
  }
  const { status, plan } = info;
  const label = document.createElement('span');
  label.textContent = status.registered
    ? `On (${status.last_result ? `last result ${status.last_result}` : 'not run yet'}) `
    : 'Off ';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn btn-small';
  button.textContent = status.registered ? 'Turn off' : 'Turn on…';
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      if (status.registered) {
        if (!window.confirm(`Remove the autostart entry ${status.entry}? Jig keeps running until you stop it.`)) return;
        await call('/autostart/disable', 'POST');
      } else {
        if (!window.confirm(disclosure(plan))) return;
        await call('/autostart/enable', 'POST', { confirm: true });
      }
      await refresh();
    } catch (err) {
      render(null, err.message);
    } finally {
      button.disabled = false;
    }
  });
  dd.append(label, button);
}

async function refresh() {
  try {
    render(await call('/autostart'));
  } catch (err) {
    render(null, err.message);
  }
}

function start() {
  const app = document.getElementById('app');
  if (!app || !cell()) return;
  const whenVisible = () => { if (!app.hidden) refresh(); };
  new MutationObserver(whenVisible).observe(app, { attributes: true, attributeFilter: ['hidden'] });
  document.getElementById('status-refresh')?.addEventListener('click', refresh);
  whenVisible();
}

start();
