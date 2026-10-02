// Start with Windows row in Settings > Startup: shows whether this Jig starts at sign-in and lets the user
// turn it on or off. Turning it on shows what will be registered and needs an explicit OK; the request then
// carries "confirm": true. An entry that starts another Jig folder is shown but never offered for removal.
// Kept separate from app.js.

const cell = () => document.getElementById('st-autostart');

async function call(path, method = 'GET', body) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${r.status}`);
  return data;
}

const confirmBox = (options) => window.jigConfirm(options);

function details(plan) {
  const list = document.createElement('ul');
  list.className = 'confirm-list';
  const lines = [
    `When: ${plan.trigger}`,
    `Runs as: ${plan.account}`,
    `Log: ${plan.log_path}`,
    ...plan.settings,
    ...(plan.tested ? [] : ['This way of starting Jig has not been tested on a real machine yet.']),
  ];
  for (const t of lines) {
    const li = document.createElement('li');
    li.textContent = t;
    list.append(li);
  }
  return list;
}

function plainLabel(status) {
  if (status.summary) return status.summary;
  if (!status.registered) return 'Off.';
  return status.last_result ? `On (last result ${status.last_result}).` : 'On. Jig will start the next time you sign in.';
}

function render(info, error) {
  const dd = cell();
  if (!dd) return;
  dd.replaceChildren();
  if (error) {
    dd.textContent = `Jig couldn't change Start with Windows: ${error}`;
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
  label.textContent = `${plainLabel(status)} `;
  dd.append(label);
  if (status.registered && status.owned === false) return;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn btn-small';
  button.textContent = status.registered ? 'Turn off' : 'Turn on…';
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      if (status.registered) {
        const ok = await confirmBox({
          title: 'Stop Jig starting with Windows?',
          body: ['Jig keeps running now. It just won’t start by itself next time you sign in.'],
          ok: 'Turn off',
        });
        if (!ok) return;
        await call('/autostart/disable', 'POST');
      } else {
        const ok = await confirmBox({
          title: 'Start Jig when you sign in?',
          body: ['Jig will start quietly in the background each time you sign in to this computer, so your schedules keep running.', details(plan),
            'You can turn this off here at any time.'],
          ok: 'Turn on',
        });
        if (!ok) return;
        await call('/autostart/enable', 'POST', { confirm: true });
      }
      await refresh();
    } catch (err) {
      render(null, err.message);
    } finally {
      button.disabled = false;
    }
  });
  dd.append(button);
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
