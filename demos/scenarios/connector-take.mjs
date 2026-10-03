/*
 * Connector takes: a real capability run (demos/connectors/captest.py --ui) filmed in the web UI of the Jig whose
 * accounts are connected. The Python side seeds the accounts, decides every approval card by the test limits,
 * checks the outcome directly with the provider and cleans up; this side types each request and clicks each card
 * in the UI. Every PASS/FAIL line of the run becomes a check of the capture, so a take passes (and can be
 * rendered) only when the capability run did.
 */
import { spawn } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { cropAround, fit, FULL, marksOf } from '../lib/edit.mjs';
import { BASE } from '../lib/jig.mjs';
import { ui } from '../lib/ui-map.mjs';
import { DEMOS, JIG_REPO } from '../lib/util.mjs';
import { answerCard, followChat } from './common.mjs';
import { backToChat, openSettings, signIn } from './common-ui.mjs';

/* both run from the demo venv (run.ps1's snapshot of HEAD), so other workers' uncommitted jig/ edits never reach them */
export const TEST_JIG = { base: 'http://127.0.0.1:8792', config: 'C:\Users\you\\.jig-connectors-test\\jig.toml', snapshot: true };
/* DEMO_JIG_PORT and DEMO_CHECKOUT_DATA: a checkout Jig of the take's own, beside one already on 8770 */
export const CHECKOUT_JIG = {
  base: BASE, config: path.join(DEMOS, 'configs', 'connectors-checkout.toml'),
  dataDir: process.env.DEMO_CHECKOUT_DATA || path.join(os.tmpdir(), 'jig-checkout', 'data'), snapshot: true,
};

const CARD_TEXT = {
  gmail_send: 'Sending an email always asks. The card shows exactly who it goes to.',
  gmail_reply: 'Replying always asks. The card shows the thread and who it goes to.',
  gmail_send_draft: 'Sending always asks. The card shows the draft exactly as it will go.',
  gmail_create_draft: 'Saving a draft asks first.',
  gcal_create_event: 'Adding to a calendar asks first. The card names the calendar.',
  outlook_create_event: 'Adding to a calendar asks first. The card names the calendar.',
  gdrive_create_file: 'Saving to Google Drive asks first.',
  onedrive_upload_file: 'Saving to OneDrive asks first.',
  github_create_issue: 'Opening an issue asks first. The card shows the repository.',
  schedule_create: 'A schedule asks first, and says when it will run.',
  browser_login: 'Signing in asks first. The card names the site.',
  browser_click: 'Each step on the site asks first.',
  browser_submit: 'Each step on the site asks first.',
  write_file: 'Saving a file asks first.',
};

/** captest.py as a child process: its JSON-line messages in order, its other output kept for the checks. */
function startRun(args) {
  const py = path.join(JIG_REPO, '.venv', 'Scripts', 'python.exe');
  const child = spawn(py, ['-u', path.join('demos', 'connectors', 'captest.py'), ...args], {
    cwd: JIG_REPO, env: { ...process.env, PYTHONUTF8: '1', PYTHONPATH: JIG_REPO }, windowsHide: true,
  });
  const queue = [];
  let waiting = null;
  const lines = [];
  const push = (m) => {
    if (waiting) {
      const w = waiting;
      waiting = null;
      w(m);
    } else queue.push(m);
  };
  readline.createInterface({ input: child.stdout }).on('line', (line) => {
    if (line.startsWith('@@UI ')) return push(JSON.parse(line.slice(5)));
    console.log(`  run   ${line}`);
    lines.push(line);
  });
  readline.createInterface({ input: child.stderr }).on('line', (line) => {
    console.error(`  run!  ${line}`);
    lines.push(line);
  });
  const exited = new Promise((resolve) => child.on('exit', (code) => {
    push({ type: 'exit', code });
    resolve(code);
  }));
  return {
    child, lines, exited,
    next: () => (queue.length ? Promise.resolve(queue.shift()) : new Promise((r) => { waiting = r; })),
    send: (m) => child.stdin.write(`${JSON.stringify(m)}\n`),
  };
}

/**
 * One take. scenario: the captest scenario; where: TEST_JIG or CHECKOUT_JIG; text: the edit's words
 * ({ title, kicker, sub, asks: [...], working, replies: [...], denied }).
 */
export function connectorTake({ scenario, where = TEST_JIG, text, instructional = false, captureOf = null }) {
  return {
    title: text.title,
    captureOf,
    external: where,
    formats: instructional ? ['landscape'] : ['landscape', 'portrait'],
    theme: 'jig-light',
    scheme: 'light',

    async capture(ctx) {
      const { jig, checks } = ctx;
      const s = await ctx.screen();
      const p = s.page;
      await signIn(ctx, s);
      await openSettings(s, 'chat');
      if (!(await ui.showWorkingSwitch(p).isChecked())) await s.click(ui.showWorkingSwitch(p));
      await backToChat(s);
      await s.startRecording();
      await ctx.sleep(1200);
      ctx.mark('start');

      const run = startRun([scenario, '--runs', '1', '--ui', '--base', where.base, '--config', where.config,
        '--token', jig.token, ...(where.snapshot ? ['--snapshot'] : [])]);
      ctx.onCleanup('the capability run', async () => { if (run.child.exitCode === null) run.child.kill(); });
      let asks = 0;
      let cards = 0;
      for (let m = await run.next(); m.type !== 'exit'; m = await run.next()) {
        if (m.type !== 'chat') throw new Error(`Unexpected message from the capability run: ${JSON.stringify(m)}`);
        asks += 1;
        const label = asks === 1 ? 'ask' : `ask${asks}`;
        ctx.mark(`${label}-start`);
        await s.type(ui.chatInput(p), m.message, { charMs: 12 });
        ctx.mark(`${label}-typed`, { box: await s.box(ui.chatInput(p)) });
        const chat = await followChat(ctx, s, {
          label, send: () => s.press('Enter'), timeoutMs: 900000,
          onApproval: async (asked) => {
            cards += 1;
            run.send({ type: 'approval', approval_id: asked.data.approval_id });
            const d = await run.next();
            if (d.type !== 'decision') throw new Error(`Expected a decision, got ${JSON.stringify(d)}`);
            ctx.mark(`card${cards}`, { at: asked.t, tool: d.tool, approve: d.approve, chat: label });
            await answerCard(ctx, s, d.tool, { label: `card${cards}`, approve: d.approve, note: d.note });
          },
        });
        ctx.mark(`${label}-reply`, { box: await s.box(ui.jigReplies(p).last()) });
        await ctx.sleep(2500);
        ctx.mark(`${label}-end`);
        run.send({ type: 'done', run_id: chat.runId });
      }
      await s.stopRecording();
      const code = await run.exited;
      for (const line of run.lines) {
        const r = line.match(/^\s+(PASS|FAIL)\s+(.*)$/);
        if (r) checks.ok(`capability: ${r[2]}`, r[1] === 'PASS');
      }
      const summary = run.lines.find((l) => /^== \S+ #1: (PASSED|FAILED)/.test(l)) || '';
      ctx.note(summary || 'the capability run printed no summary');
      checks.ok('the capability run passed', code === 0 && /PASSED/.test(summary), summary || `exit ${code}`);
    },

    edit(m, format) {
      return { shots: takeShots(m, format, text, instructional) };
    },
  };
}

function takeShots(m, format, text, instructional) {
  const k = marksOf(m);
  const portrait = format === 'portrait';
  const aspect = portrait ? 1032 / 774 : 16 / 9;
  const header = (title) => (portrait ? { kicker: `Jig · ${text.kicker}`, title } : null);
  /* the instructional cut keeps a calm pace: lower speed-ups and longer holds */
  const pace = (n) => (instructional ? Math.round(n * 1.8) : n);
  const step = (n, words) => (instructional ? `Step ${n}. ${words}` : words);
  const shots = [{ type: 'card', dur: instructional ? 4.5 : 3.2, card: { kicker: instructional ? 'Jig · how to' : 'Jig · real run',
    title: text.title, sub: text.sub, avatar: { state: 'thinking' } } }];
  const cards = m.marks.filter((x) => /^card\d+$/.test(x.id));
  let n = 0;
  const labels = ['ask', 'ask2', 'ask3', 'ask4'].filter((l) => k.has(`${l}-sent`));
  labels.forEach((label, i) => {
    const sent = k.t(`${label}-sent`);
    shots.push({ type: 'screen', chapter: 'Ask', header: header('*Ask*'), from: k.t(`${label}-start`), to: sent + 1200,
      speed: fit(k.t(`${label}-start`), sent + 1200, pace(portrait ? 5 : 6)),
      crops: [{ at: k.t(`${label}-start`), box: portrait ? cropAround(k.box(`${label}-typed`), aspect, { pad: 120 }) : FULL, ease: 0.01 }],
      captions: [{ at: k.t(`${label}-start`), text: step(++n, text.asks?.[i] || 'Ask in plain words.') }] });
    let t = sent + 1200;
    for (const c of cards.filter((x) => x.chat === label)) {
      const shown = k.t(`${c.id}-card`);
      const answered = k.t(`${c.id}-answer`);
      if (shown - 300 > t + 800) {
        shots.push({ type: 'screen', chapter: 'Jig works', header: header('It *works*'), from: t, to: shown - 300,
          speed: fit(t, shown - 300, pace(8)), captions: [{ at: t, text: step(++n, text.working || 'Jig reads what it needs from your account. Reading never needs a yes.') }] });
      }
      const words = c.approve ? (CARD_TEXT[c.tool] || 'Anything that changes your account waits for your yes.')
        : (text.denied || 'This one is outside what you allowed, so the answer is *no*.');
      shots.push({ type: 'screen', chapter: 'It asks first', header: header('It *asks* first'), from: shown - 300, to: answered + 1500,
        speed: fit(shown - 300, answered + 1500, pace(9)), sfx: [{ at: c.at, kind: 'approval' }],
        crops: [{ at: shown - 300, box: portrait ? cropAround(k.box(`${c.id}-card`), aspect, { pad: 40 }) : FULL, ease: 0.01 }],
        captions: [{ at: shown - 300, text: step(++n, words) }, { at: answered - 600, text: c.approve ? 'Yes.' : 'No.' }] });
      t = answered + 1500;
    }
    const done = k.t(`${label}-done`);
    if (done > t + 800) {
      shots.push({ type: 'screen', chapter: 'Jig works', header: header('It *works*'), from: t, to: done,
        speed: fit(t, done, pace(8)), captions: [{ at: t, text: step(++n, text.working || 'Jig reads what it needs from your account. Reading never needs a yes.') }] });
    }
    const end = k.t(`${label}-end`);
    shots.push({ type: 'screen', chapter: 'Done', header: header('*Done*'), from: Math.max(done, t), to: end,
      speed: fit(Math.max(done, t), end, pace(5)), sfx: [{ at: done, kind: 'success' }],
      crops: [{ at: Math.max(done, t), box: portrait ? cropAround(k.box(`${label}-reply`), aspect, { pad: 60 }) : FULL, ease: 0.01 }],
      captions: [{ at: Math.max(done, t), text: step(++n, text.replies?.[i] || 'Done. The result was checked in the real account.') }] });
  });
  shots.push({ type: 'card', dur: instructional ? 4.5 : 3.6, card: { title: '*Jig*', sub: 'Your local, always-on agent. Open source.',
    lines: portrait ? [] : ['Your accounts, your rules: it asks before it changes anything'], avatar: { state: 'dance' } } });
  return shots;
}
