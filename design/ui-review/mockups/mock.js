/* UI review mockups: three directions for a larger, always-present Jig, plus the coding work view.
 * mock.html?dir=a|b|c&screen=home|chat|approval|work|work-open|success|setup|settings|off&theme=light|dark&size=desktop|narrow|compact
 * Uses the real style.css, fonts and <jig-avatar> component; everything new is in mock.css.
 */
import '/avatar/jig-avatar.js';

const root = document.documentElement;
const { dir: DIR, theme: THEME, size: SIZE } = root.dataset;
/* work-tests is work-open with the test results expanded instead of the change */
const SCREEN = root.dataset.screen === 'work-tests' ? 'work-open' : root.dataset.screen;
const OPEN = root.dataset.screen === 'work-tests' ? { openTests: true, openDiff: false } : { openTests: false, openDiff: true };
const SMALL = SIZE !== 'desktop';

/* ---------- avatar ---------- */

const POSE_T = { idle: 3, thinking: 2.5, working: 2.5, approval: 1.2, success: 4.6, error: 4, paused: 3, talking: 3, dance: 3 };

/** A real avatar, posed on a manual clock so every render is the same frame. pose: "idle" or "working:coding". */
const av = (pose, cls = '', label = '') => `<jig-avatar class="${cls}" data-pose="${pose}"${label ? ` data-label="${label}"` : ''}></jig-avatar>`;

async function poseAvatars() {
  for (const a of document.querySelectorAll('jig-avatar[data-pose]')) {
    a.setAttribute('clock', 'manual');
    a.setAttribute('seed', '7');
    a.setAttribute('framing', 'full');
    a.setAttribute('shape', 'none');
    a.setAttribute('theme', THEME);
  }
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  for (const a of document.querySelectorAll('jig-avatar[data-pose]')) {
    const [state, task] = a.dataset.pose.split(':');
    a.setState(state, task ? { task } : {});
    a.advance(POSE_T[state] ?? 3, 60);
  }
}

/* ---------- shared pieces (real classes from style.css where they exist) ---------- */

const GEAR = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="currentColor" d="M12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7Zm8.6-2.3.1-1.2-.1-1.2 2-1.6c.2-.1.2-.4.1-.6l-1.9-3.3c-.1-.2-.4-.3-.6-.2l-2.4 1a7 7 0 0 0-2-1.2L15.4 2c0-.2-.2-.4-.5-.4h-3.8c-.2 0-.4.2-.5.4l-.4 2.6c-.7.3-1.4.7-2 1.2l-2.4-1c-.2-.1-.5 0-.6.2L3.3 8.3c-.1.2-.1.5.1.6l2 1.6-.1 1.2.1 1.2-2 1.6c-.2.1-.2.4-.1.6l1.9 3.3c.1.2.4.3.6.2l2.4-1c.6.5 1.3.9 2 1.2l.4 2.6c0 .2.2.4.5.4h3.8c.2 0 .4-.2.5-.4l.4-2.6c.7-.3 1.4-.7 2-1.2l2.4 1c.2.1.5 0 .6-.2l1.9-3.3c.1-.2.1-.5-.1-.6l-2-1.6Z"/></svg>';

const header = (dot = 'ok') => `<header class="top">
  <h1 class="wordmark">Jig</h1>
  <div class="top-actions">
    <span class="health" data-tone="${dot}"><span class="health-dot" aria-hidden="true"></span><span class="sr-only">Live</span></span>
    <a class="icon-btn" href="#settings" aria-label="Settings">${GEAR}</a>
  </div>
</header>`;

const composer = (placeholder = 'Message Jig', disabled = false) => `<form class="chat-form">
  <div class="composer"><label class="sr-only" for="m">Message</label><textarea id="m" rows="1" placeholder="${placeholder}"></textarea>
  <button type="button" class="btn btn-primary"${disabled ? ' disabled' : ''}>Send</button></div>
</form>`;

const msg = (role, html, extra = '') => `<article class="msg ${role}${extra}" aria-label="${role === 'user' ? 'You said' : 'Jig replied'}"><div class="content md">${html}</div></article>`;

/** The one-line caption that is the avatar's text equivalent: role=status, announced politely. */
const say = (text, cls = '') => `<p class="say ${cls}" role="status" aria-live="polite">${text}</p>`;

const EXAMPLES = ['What\u2019s in the news today?', 'Remember that I prefer short answers', 'Every weekday at 8am, summarise the BBC headlines'];
const examples = () => `<div class="examples" role="group" aria-label="Things to try">${EXAMPLES.map((e) => `<button type="button" class="btn btn-small">${e}</button>`).join('')}</div>`;

const glance = () => `<div class="glance">
  <div class="glance-card"><p class="glance-h">On the go</p><p class="glance-t">Nothing right now</p></div>
  <div class="glance-card"><p class="glance-h">Coming up</p><p class="glance-t">Morning headlines</p><p class="glance-s">Weekdays at 08:00</p></div>
</div>`;

/* Conversations */
const WEATHER = [
  msg('user', '<p>Morning! What\u2019s the weather like in Bath this weekend?</p>'),
  msg('jig', '<p>Saturday looks dry and mild, about 17\u00b0C with light cloud. Sunday has showers from midday, so Saturday\u2019s the better day to be out.</p>'),
];
const WALK_ASK = msg('user', '<p>Can you find a gentle walk near Bath for Saturday? Nothing too hilly, and somewhere with a caf\u00e9 on the way.</p>');
const WALK_PARTIAL = msg('jig', '<p>I\u2019ve found a couple of promising ones so far. Now I\u2019m checking which have a caf\u00e9 on the way\u2026</p>', ' streaming');
const WALK_ANSWER = msg('jig', '<p>The <strong>Kennet and Avon canal walk</strong> from Bath to Dundas Aqueduct is flat all the way, with a caf\u00e9 at the wharf. Shall I ask Sam if he\u2019s free?</p>');
const EMAIL_ASK = msg('user', '<p>Yes please, the canal one sounds perfect. Can you email Sam and ask?</p>');
const CAL_ASK = msg('user', '<p>Sam\u2019s in! Put it in my calendar please.</p>');
const CODE_ASK = msg('user', '<p>My budget.py adds up the wrong total when an amount has a \u00a3 sign. Can you fix it and check it works?</p>');
const CODE_ACK = msg('jig', '<p>On it. I\u2019ll look at the script, run its tests and fix whatever\u2019s wrong. You can watch the steps if you\u2019re curious.</p>');

/* The approval card, built from the real .ask classes. */
function approvalCard({ cls = '', tail = '' } = {}) {
  return `<div class="ask is-pending held ${cls}" data-risk="medium" role="group" aria-labelledby="ask-t" ${tail ? `data-tail="${tail}"` : ''}>
    <div class="ask-head"><span class="ask-mark" aria-hidden="true">?</span><strong class="ask-title" id="ask-t">Can I send this email to Sam?</strong></div>
    <p class="will">If you say yes, I\u2019ll send it from your Gmail.</p>
    <div class="preview"><p><b>To:</b> Sam Patel<br><b>Subject:</b> Saturday walk</p><p>Hi Sam, fancy the canal walk to Dundas Aqueduct on Saturday? It\u2019s flat, with a caf\u00e9 at the wharf. Meet at Bath Spa station at 10?</p></div>
    <div class="decide"><button type="button" class="btn btn-yes">Yes, send it</button><button type="button" class="btn btn-no">No</button></div>
    <details class="why-ask"><summary>Why am I asking?</summary></details>
  </div>`;
}

const doneRecord = () => `<div class="done-card" role="group" aria-label="Added to your calendar">
  <span class="done-mark" aria-hidden="true">\u2713</span>
  <div><p class="done-t">Saturday walk with Sam</p><p class="done-s">Saturday 10:00 \u00b7 Bath Spa station \u00b7 Google Calendar</p></div>
</div>`;

/* ---------- the coding work view ---------- */

const META = { total: 8, passed: 6, failed: 2 };

/** Level 0: one friendly line, a test meter when there are tests, and a way in. */
function workSummary({ open = false, compact = false } = {}) {
  const pct = (META.passed / META.total) * 100;
  return `<section class="work${compact ? ' compact' : ''}" aria-label="What Jig is doing">
    <p class="work-line"><span class="pulse" aria-hidden="true"></span><span>2 tests failed, so I\u2019m fixing them now.</span></p>
    <div class="meter-row">
      <div class="meter" role="img" aria-label="${META.passed} of ${META.total} tests passing"><span class="m-ok" style="width:${pct}%"></span><span class="m-bad" style="width:${100 - pct}%"></span></div>
      <p class="meter-text"><span class="pass">${META.passed} passed</span> \u00b7 <span class="fail">${META.failed} to fix</span></p>
    </div>
    <button type="button" class="btn btn-small work-more" aria-expanded="${open}">${open ? 'Hide the steps' : 'Show the steps'} <span class="count">4 so far</span></button>
  </section>`;
}

const RESULTS = [
  ['ok', 'Plain amounts add up', 'test_plain_amounts'],
  ['ok', 'Empty rows are skipped', 'test_blank_rows'],
  ['ok', 'Negative amounts count as spending', 'test_negative'],
  ['ok', 'Commas in thousands', 'test_thousands'],
  ['ok', 'Reads the CSV file', 'test_reads_csv'],
  ['ok', 'Totals by category', 'test_by_category'],
  ['bad', 'Amounts with a \u00a3 sign', 'test_pound_sign', '\u201c\u00a312.50\u201d couldn\u2019t be read as a number'],
  ['bad', 'March total with \u00a3 signs', 'test_march_total', 'Stopped at the same \u00a3 sign'],
];

function testsDetail() {
  return `<div class="step-detail">
    <div class="cmd"><p class="cmd-label">What I ran, in my sandbox</p><code class="cmd-line">python -m pytest -q</code><p class="cmd-why">Runs the checks that came with your script.</p></div>
    <ul class="results" aria-label="Test results">${RESULTS.map(([k, name, id, why]) => `<li class="r ${k}"><span class="r-mark" aria-hidden="true">${k === 'ok' ? '\u2713' : '\u2715'}</span><span class="r-name">${name}${k === 'ok' ? '' : '<span class="sr-only">: failed</span>'}</span><code class="r-id">${id}</code>${why ? `<span class="r-why">${why}</span>` : ''}</li>`).join('')}</ul>
    <button type="button" class="btn-link">Show the full output</button>
  </div>`;
}

const BEFORE = [
  ['ctx', 12, 'def parse_amount(text):'],
  ['del', 13, '    return float(text.strip())'],
  ['ctx', 14, ''],
  ['ctx', 15, 'def total(rows):'],
];
const AFTER = [
  ['ctx', 12, 'def parse_amount(text):'],
  ['add', 13, '    cleaned = text.strip().lstrip("\u00a3").replace(",", "")'],
  ['add', 14, '    return float(cleaned)'],
  ['ctx', 15, ''],
  ['ctx', 16, 'def total(rows):'],
];
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
const diffLines = (lines) => lines.map(([k, n, t]) => `<span class="dl ${k}"><span class="dn">${n}</span><span class="dm" aria-hidden="true">${k === 'del' ? '\u2212' : k === 'add' ? '+' : ''}</span><span class="dt">${esc(t) || ' '}</span>${k === 'del' ? '<span class="sr-only"> (removed)</span>' : k === 'add' ? '<span class="sr-only"> (added)</span>' : ''}</span>`).join('');

function diffDetail(layout = 'side') {
  return `<div class="step-detail">
    <p class="change-plain">Amounts like \u201c\u00a31,250.00\u201d are now read as 1250 before they\u2019re added up.</p>
    <div class="diff ${layout}" role="group" aria-label="The change to budget.py, before and after">
      <div class="diff-col before"><p class="diff-head"><span class="dh-dot" aria-hidden="true"></span>Before</p><pre>${diffLines(BEFORE)}</pre></div>
      <div class="diff-col after"><p class="diff-head"><span class="dh-dot" aria-hidden="true"></span>After</p><pre>${diffLines(AFTER)}</pre></div>
    </div>
    <button type="button" class="btn-link">Open budget.py</button>
  </div>`;
}

/** Level 1 and 2: every step in plain words, with the technical detail one tap further in. */
function stepsList({ layout = 'side', openTests = OPEN.openTests, openDiff = OPEN.openDiff } = {}) {
  const step = (cls, mark, title, sub, toggle, detail = '') => `<li class="step ${cls}">
    <span class="step-mark" aria-hidden="true">${mark}</span>
    <div class="step-body"><div class="step-top"><p class="step-title">${title}</p>${toggle ? `<button type="button" class="step-toggle" aria-expanded="${Boolean(detail)}">${detail ? 'Hide' : toggle}</button>` : ''}</div>
    ${sub ? `<p class="step-sub">${sub}</p>` : ''}${detail}</div></li>`;
  return `<ol class="steps-list" aria-label="Steps so far">
    ${step('done', '\u2713', 'Looked at your files', 'budget.py, test_budget.py and march.csv', 'Show')}
    ${step('done', '\u2713', 'Ran the tests', `<span class="pill ok">6 passed</span><span class="pill bad">2 failed</span>`, 'Show', openTests ? testsDetail() : '')}
    ${step('done', '\u2713', 'Found the problem', 'Amounts with a \u00a3 sign weren\u2019t being turned into numbers.', '')}
    ${step('done', '\u2713', 'Changed budget.py', '2 lines changed', 'See the change', openDiff ? diffDetail(layout) : '')}
    ${step('now', '<span class="spin"></span>', 'Running the tests again\u2026', '', '')}
  </ol>`;
}

/* ---------- settings, set-up and off bodies ---------- */

const NAV = ['Model and connection', 'What Jig can do on its own', 'Connections', 'Memory and notes', 'Conversations and jobs', 'Schedules', 'History', 'Starting with Windows', 'Use Jig from your other devices', 'Turn Jig off', 'Appearance', 'In the conversation'];
const settingsNav = () => `<nav class="settings-nav" aria-label="Settings sections"><ul>${NAV.map((n, i) => `<li><a href="#"${i === 1 ? ' aria-current="page"' : ''}>${n}</a></li>`).join('')}</ul></nav>`;
const CHOICES = [
  ['Read web pages', 'Look things up and read pages', 'On its own'],
  ['Look through your email', 'Search and read, never send', 'On its own'],
  ['Send email', 'From your Gmail', 'Ask me first'],
  ['Change your calendar', 'Add, move or cancel events', 'Ask me first'],
  ['Run code in its sandbox', 'Commands and scripts, kept away from your files', 'Ask me first'],
];
const settingsSection = () => `<section class="set-section" aria-labelledby="rules-h">
  <h3 id="rules-h">What Jig can do on its own</h3>
  <p class="hint">Choose what Jig may do without asking you first. Whatever you choose, the Sentinel still checks anything that changes or sends something, and the core rules always apply.</p>
  <div class="choices">${CHOICES.map(([n, d, v]) => `<div class="choice"><div class="choice-text"><p class="choice-name">${n}</p><p class="hint-quiet">${d}</p></div><select aria-label="${n}"><option>${v}</option></select></div>`).join('')}</div>
  <h4>Core rules: always on</h4>
  <ul class="core-list"><li>Jig always asks before it spends money.</li><li>Jig never shares your passwords with anyone, or with a website you didn\u2019t choose.</li></ul>
</section>`;

const setupCard = () => `<section class="setup-card" aria-labelledby="setup-h">
  <h3 id="setup-h">Model apps on this computer</h3>
  <p class="hint-quiet">llama.cpp, at http://127.0.0.1:8080/v1</p>
  <label class="model-pick"><input type="radio" name="m" checked> <span><strong>The model in llama.cpp</strong> <span class="tag">Loaded</span><span class="tag">64K context</span><span class="hint-quiet">Runs on this computer. Nothing you say leaves it.</span></span></label>
  <button type="button" class="btn btn-primary">Check it and start</button>
  <details class="advanced"><summary>I don\u2019t have a model app yet</summary></details>
  <details class="advanced"><summary>Use a cloud model instead</summary></details>
</section>`;

const offBody = () => `<div class="off-body">
  <h2>Jig is off</h2>
  <p>It finished what it was doing and saved where it got to. It\u2019ll pick up from there when you turn it on.</p>
  <a class="btn btn-primary" href="#">Turn Jig on</a>
  <p class="hint-quiet">This page reconnects by itself when Jig is running again.</p>
</div>`;

/* ---------- Direction A: side by side. Jig has its own corner, always, beside the conversation. ---------- */

function dirA() {
  const POSES = { home: 'idle', chat: 'working:browsing', approval: 'approval', work: 'working:coding', 'work-open': 'working:coding', success: 'success', setup: 'idle', settings: 'idle', off: 'paused' };
  const SAYS = {
    home: 'Ready when you are.',
    chat: 'Reading walking guides for Bath\u2026',
    approval: 'Can you check this first?',
    work: 'Working on budget.py\u2026',
    'work-open': 'Working on budget.py\u2026',
    success: 'Done! It\u2019s in your calendar.',
    setup: 'Hi! I\u2019m Jig. I need a model to think with.',
    settings: 'Here you choose what I can do without asking you first.',
    off: 'Zzz\u2026 I\u2019m switched off.',
  };
  const pose = POSES[SCREEN];

  const cornerBody = {
    home: () => `${glance()}<p class="corner-tip">Tip: ask me to do something every morning and I\u2019ll set up a schedule.</p>`,
    chat: () => `<div class="mini-status"><p class="mini-h">This conversation</p><p>Looking for walks near Bath</p></div>${glance()}`,
    approval: () => approvalCard({ tail: 'up' }),
    work: () => workSummary(),
    'work-open': () => workSummary({ open: true }),
    success: () => doneRecord(),
    setup: () => `<ol class="setup-steps"><li class="on">Choose a model</li><li>I check it works</li><li>Say hello</li></ol><p class="corner-tip">Everything stays on this computer unless you choose a cloud model.</p>`,
    settings: () => `<p class="corner-tip">\u201cAsk me first\u201d is the safest choice. You can change any of these at any time.</p>`,
    off: () => '<p class="corner-tip">Turn me on and I\u2019ll carry on from where I left off. Your conversations and notes are all still here.</p>',
  }[SCREEN]();

  const foot = ['setup', 'off', 'settings'].includes(SCREEN) ? '' : `<div class="corner-foot"><button type="button" class="btn btn-small">Pause Jig</button><button type="button" class="btn btn-small btn-quiet">What Jig\u2019s up to</button></div>`;

  const main = {
    home: () => `<div class="convo"><div class="log log-empty"><div class="welcome-a"><p class="welcome-title">Hello! What shall we do?</p><p class="hint">Ask me anything, or give me something to do. I\u2019ll always check with you before I change, send or spend anything.</p>${examples()}</div></div>${composer()}</div>`,
    chat: () => `<div class="convo"><div class="log">${WEATHER.join('')}${WALK_ASK}${WALK_PARTIAL}</div>${composer()}</div>`,
    approval: () => `<div class="convo"><div class="log">${WALK_ASK}${WALK_ANSWER}${EMAIL_ASK}${msg('jig', '<p>Here\u2019s what I\u2019d send. I\u2019m holding it up next to me so you can check it.</p>')}<p class="waiting-pill" role="note"><span aria-hidden="true">\u2190</span> Waiting for your answer next to Jig</p></div>${composer()}</div>`,
    work: () => `<div class="convo"><div class="log">${CODE_ASK}${CODE_ACK}</div>${composer()}</div>`,
    'work-open': () => `<div class="convo"><div class="tabs" role="tablist"><button role="tab" aria-selected="false" class="tab">Conversation</button><button role="tab" aria-selected="true" class="tab">Steps <span class="count">4</span></button></div><div class="log steps-pane">${stepsList({ layout: 'side' })}</div>${composer()}</div>`,
    success: () => `<div class="convo"><div class="log">${EMAIL_ASK}${msg('jig', '<p>Sent! Sam said yes. \ud83c\udf89</p>')}${CAL_ASK}${msg('jig', '<p>Done! It\u2019s in your calendar for Saturday at 10:00, with the meeting point.</p>')}</div>${composer()}</div>`,
    setup: () => `<div class="convo setup-main">${setupCard()}</div>`,
    settings: () => `<div class="convo settings-main"><div class="settings-head"><a class="btn btn-small btn-quiet" href="#">\u2190 Back to chat</a><h2>Settings</h2></div><div class="settings-grid">${settingsNav()}<div class="settings-body">${settingsSection()}</div></div></div>`,
    off: () => `<div class="convo off-main">${offBody()}</div>`,
  }[SCREEN]();

  if (!SMALL) {
    return `${header(SCREEN === 'off' ? 'muted' : 'ok')}<div class="a-shell${SCREEN === 'settings' ? ' wide' : ''}${SCREEN === 'approval' ? ' asking' : ''}">
      <aside class="corner" aria-label="Jig">
        <div class="corner-stage">${av(pose, 'corner-av')}</div>
        ${say(SAYS[SCREEN], 'tail-up')}
        <div class="corner-body">${cornerBody}</div>
        ${foot}
      </aside>
      <main class="a-main">${main}</main>
    </div>`;
  }

  /* narrow: Jig moves to a band across the top, never smaller than 112px; the context block becomes a sheet */
  if (SCREEN === 'home') {
    return `${header()}<main class="a-narrow home"><div class="a-hero">${av('idle', 'hero-av')}${say('Ready when you are.', 'tail-up')}</div>
      <div class="welcome-a"><p class="welcome-title">Hello! What shall we do?</p><p class="hint">Ask me anything, or give me something to do. I\u2019ll always check with you before I change, send or spend anything.</p>${examples()}</div>
      <div class="spacer"></div>${composer()}</main>`;
  }
  const band = (p, text, body = '') => `<section class="a-band" aria-label="Jig">${av(p, 'band-av')}<div class="band-text">${say(text, 'tail-left')}${body}</div></section>`;
  if (SCREEN === 'chat') return `${header()}<main class="a-narrow">${band(pose, SAYS.chat)}<div class="log">${WEATHER.join('')}${WALK_ASK}${WALK_PARTIAL}</div>${composer()}</main>`;
  if (SCREEN === 'success') return `${header()}<main class="a-narrow">${band(pose, SAYS.success)}<div class="log">${CAL_ASK}${msg('jig', '<p>Done! It\u2019s in your calendar for Saturday at 10:00, with the meeting point.</p>')}${doneRecord()}</div>${composer()}</main>`;
  if (SCREEN === 'work') return `${header()}<main class="a-narrow">${band(pose, SAYS.work, workSummary({ compact: true }))}<div class="log">${CODE_ASK}${CODE_ACK}</div>${composer()}</main>`;
  if (SCREEN === 'approval') {
    return `${header()}<main class="a-narrow dimmed"><div class="log">${WALK_ANSWER}${EMAIL_ASK}${msg('jig', '<p>Here\u2019s what I\u2019d send.</p>')}</div></main>
      <div class="sheet" role="dialog" aria-label="Jig has a question"><div class="sheet-peek">${av('approval', 'peek-av')}${say('Can you check this first?', 'tail-left small')}</div>${approvalCard()}</div>`;
  }
  if (SCREEN === 'work-open') {
    return `${header()}<main class="a-narrow dimmed"><div class="log">${CODE_ASK}</div></main>
      <div class="sheet tall" role="dialog" aria-label="Steps so far"><div class="sheet-peek">${av('working:coding', 'peek-av')}${say('2 tests failed, so I\u2019m fixing them now.', 'tail-left small')}</div>
      <div class="sheet-head"><h2>Steps so far</h2><button type="button" class="btn btn-small btn-quiet">Close</button></div>${stepsList({ layout: 'stacked' })}</div>`;
  }
  return '';
}

/* ---------- Direction B: Jig talks to you. Jig stands on a stage by the message box and speaks in a bubble. ---------- */

function dirB() {
  const stage = (pose, bubble, cls = '') => `<section class="b-stage ${cls}" aria-label="Jig">${av(pose, 'stage-av')}<div class="b-speech-wrap">${bubble}</div></section>`;
  const speech = (inner, cls = '') => `<div class="b-speech ${cls}">${inner}</div>`;
  const live = (text) => `<p class="b-live" role="status" aria-live="polite">${text}</p>`;
  const wrap = (transcript, stageHtml, opts = {}) => `${header(opts.dot || 'ok')}<main class="b-wrap${opts.cls ? ` ${opts.cls}` : ''}">
    <div class="b-transcript" aria-label="Conversation so far">${transcript}</div>${stageHtml}${opts.noComposer ? '' : composer()}</main>`;

  switch (SCREEN) {
    case 'home':
      return wrap('', stage('idle', speech(`${live('Hello! What shall we do?')}<p class="b-sub">Ask me anything, or give me something to do. I\u2019ll always check with you before I change, send or spend anything.</p>${examples()}`), 'hero'), { cls: 'empty' });
    case 'chat':
      return wrap(`${WEATHER.join('')}${WALK_ASK}`, stage('working:browsing', speech(`${live('Reading walking guides for Bath\u2026')}<p class="b-reply">I\u2019ve found a couple of promising ones so far. Now I\u2019m checking which have a caf\u00e9 on the way<span class="typing"></span></p>`)));
    case 'approval':
      return wrap(`${WALK_ANSWER}${EMAIL_ASK}`, stage('approval', speech(`${live('Can I send this email to Sam?')}${approvalCard({ cls: 'in-speech' })}`, 'is-ask')), { cls: 'focus' });
    case 'work':
      return wrap(`${WEATHER[1]}${CODE_ASK}`, stage('working:coding', speech(`${workSummary()}`)));
    case 'work-open':
      return wrap('', `<section class="b-steps" aria-label="Steps so far"><div class="sheet-head"><h2>Steps so far</h2><button type="button" class="btn btn-small btn-quiet">Hide the steps</button></div>${stepsList({ layout: SMALL ? 'stacked' : 'side' })}</section>${stage('working:coding', speech(`<p class="work-line"><span class="pulse" aria-hidden="true"></span><span role="status">2 tests failed, so I\u2019m fixing them now.</span></p>`, 'slim'), 'slim')}`, { cls: 'steps-open' });
    case 'success':
      return wrap(`${EMAIL_ASK}${msg('jig', '<p>Sent! Sam said yes.</p>')}${CAL_ASK}`, stage('success', speech(`${live('Done! It\u2019s in your calendar.')}${doneRecord()}`, 'is-done')));
    case 'setup':
      return `${header('warn')}<main class="b-wrap setup">${stage('idle', speech(`${live('Hi! I\u2019m Jig.')}<p class="b-sub">I need a model to think with. I found one on this computer. Shall I check it works?</p>${setupCard()}`, 'big'), 'hero')}</main>`;
    case 'settings':
      return `${header()}<main class="settings b-settings"><div class="settings-head"><a class="btn btn-small btn-quiet" href="#">\u2190 Back to chat</a><h2>Settings</h2></div>
        ${stage('idle', speech(`${live('Here you choose what I can do without asking you first.')}<p class="b-sub">\u201cAsk me first\u201d is the safest choice. You can change any of these at any time.</p>`), 'slim guide')}
        <div class="settings-grid">${settingsNav()}<div class="settings-body">${settingsSection()}</div></div></main>`;
    case 'off':
      return `${header('muted')}<main class="b-wrap setup">${stage('paused', speech(`${live('Zzz\u2026 I\u2019m switched off.')}${offBody()}`, 'big'), 'hero')}</main>`;
    default: return '';
  }
}

/* ---------- Direction C: Jig's home. A big hello, a perch beside the message box, and Jig steps forward for moments. ---------- */

function dirC() {
  const perch = (pose, bubble) => `<aside class="perch" aria-label="Jig">${bubble}${av(pose, 'perch-av')}</aside>`;
  const pbubble = (inner, cls = '') => `<div class="perch-say ${cls}">${inner}</div>`;
  const live = (text) => `<p class="b-live" role="status" aria-live="polite">${text}</p>`;
  const chatPage = (log, perchHtml, extra = '', cls = '') => `${header()}<main class="c-chat ${cls}"><div class="c-col"><div class="log">${log}</div>${composer()}</div>${perchHtml}</main>${extra}`;

  switch (SCREEN) {
    case 'home':
      return `${header()}<main class="c-home">${av('idle', 'hero-av')}<h2 class="c-hello" role="status">Good evening! What shall we do?</h2>
        <div class="c-compose">${composer('Ask me anything, or give me something to do')}</div>${examples()}${glance()}</main>`;
    case 'chat':
      return chatPage(`${WEATHER.join('')}${WALK_ASK}${WALK_PARTIAL}`, perch('working:browsing', pbubble(live('Reading walking guides for Bath\u2026'))));
    case 'work':
      return chatPage(`${CODE_ASK}${CODE_ACK}`, perch('working:coding', pbubble(workSummary({ compact: true }), 'wide')));
    case 'work-open':
      return chatPage(`${CODE_ASK}${CODE_ACK}`, '', `<aside class="bench" role="dialog" aria-label="Jig\u2019s workbench">
        <div class="bench-top">${av('working:coding', 'bench-av')}<div>${live('2 tests failed, so I\u2019m fixing them now.')}<p class="hint-quiet">Fixing budget.py \u00b7 6 of 8 tests passing</p></div><button type="button" class="btn btn-small btn-quiet bench-close">Close</button></div>
        ${stepsList({ layout: 'stacked' })}</aside>`, 'with-bench');
    case 'approval':
      return chatPage(`${WALK_ANSWER}${EMAIL_ASK}${msg('jig', '<p>Here\u2019s what I\u2019d send.</p>')}`, '', `<div class="spotlight" role="dialog" aria-modal="true" aria-label="Jig has a question">
        <div class="spot-inner">${av('approval', 'spot-av')}${approvalCard({ tail: 'up', cls: 'spot-card' })}<p class="hint-quiet spot-note">Not sure? Choose No and I\u2019ll leave it.</p></div></div>`, 'behind');
    case 'success':
      return chatPage(`${EMAIL_ASK}${msg('jig', '<p>Sent! Sam said yes.</p>')}${CAL_ASK}${msg('jig', '<p>Done! It\u2019s in your calendar for Saturday at 10:00, with the meeting point.</p>')}`, '', `<div class="spotlight soft" role="status" aria-label="Done">
        <div class="spot-inner">${av('success', 'spot-av big')}<p class="spot-say">Done! It\u2019s in your calendar.</p>${doneRecord()}<p class="hint-quiet">This goes away by itself, or press Esc.</p></div></div>`, 'behind');
    case 'setup':
      return `${header('warn')}<main class="c-home setup">${av('idle', 'hero-av')}<h2 class="c-hello" role="status">Hi! I\u2019m Jig. I need a model to think with.</h2>${setupCard()}</main>`;
    case 'settings':
      return `${header()}<main class="settings c-settings"><div class="settings-head"><a class="btn btn-small btn-quiet" href="#">\u2190 Back to chat</a><h2>Settings</h2></div>
        <div class="settings-grid">${settingsNav()}<div class="settings-body">${settingsSection()}</div></div></main>${perch('idle', pbubble(live('\u201cAsk me first\u201d is the safest choice.')))}`;
    case 'off':
      return `${header('muted')}<main class="c-home off">${av('paused', 'hero-av')}${offBody()}</main>`;
    default: return '';
  }
}

document.body.innerHTML = { a: dirA, b: dirB, c: dirC }[DIR]();
await document.fonts.ready;
await poseAvatars();
await new Promise((r) => setTimeout(r, 150));
window.__ready = true;
