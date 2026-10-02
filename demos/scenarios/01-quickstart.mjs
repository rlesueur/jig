/*
 * Demo 1: Quick start (host install). Real `jig health`, `jig serve` and `jig token show` in a real
 * console; then the real web UI: sign in with that token, the status panel and a first chat.
 * The Docker Compose path is recorded only once it has landed in the repository (see composeStatus).
 */
import { spawnSync } from 'node:child_process';
import { BASE } from '../lib/jig.mjs';
import { avatarState, ui } from '../lib/ui-map.mjs';
import { cropAround, fit, FULL, marksOf } from '../lib/edit.mjs';
import { union } from './common-ui.mjs';
import { JIG_REPO, waitUntil } from '../lib/util.mjs';
import { followChat, parseJsonBlock } from './common.mjs';

const FIRST_MESSAGE = 'Hi Jig! In one short sentence, what can you help me with?';

/** Whether the Compose deployment exists in the committed tree. */
export function composeStatus() {
  const r = spawnSync('git', ['-C', JIG_REPO, 'ls-tree', '--name-only', 'HEAD'], { encoding: 'utf8' });
  const names = r.stdout.split('\n');
  const found = ['compose.yaml', 'compose.yml', 'docker-compose.yml', 'docker-compose.yaml', 'deploy'].filter((n) => names.includes(n));
  return { landed: found.length > 0, found };
}

export default {
  title: 'Quick start',

  async capture(ctx) {
    const { jig, checks } = ctx;
    const compose = composeStatus();
    ctx.note(compose.landed ? `Compose deployment found in HEAD: ${compose.found.join(', ')}` : 'Compose deployment has not landed in HEAD; that segment is pending');

    const sh = await ctx.term('host', { title: 'Windows PowerShell' });
    ctx.mark('health-start');
    const health = await sh.typeCommand('jig health', { timeoutMs: 240000 });
    const report = parseJsonBlock(health.output);
    checks.ok('jig health: agent tool calling passed', report.agent?.tool_calling === true, report.agent);
    checks.ok('jig health: agent structured output passed', report.agent?.structured_output === true, report.agent);
    checks.ok('jig health: sentinel structured output passed', report.sentinel?.structured_output === true, report.sentinel);
    if ('vision' in (report.agent || {})) checks.ok('jig health: vision passed', report.agent.vision === true, report.agent);
    ctx.mark('health-end');

    ctx.mark('serve-start');
    await jig.start({ term: sh });
    await sh.expect(/Application startup complete/, { what: 'uvicorn start-up' });
    ctx.mark('serve-ready');

    const sh2 = await ctx.term('token', { title: 'Windows PowerShell (second tab)' });
    ctx.mark('token-start');
    const tok = await sh2.typeCommand('jig token show');
    checks.equal('jig token show prints the token the server accepts', tok.output.match(/[A-Za-z0-9_-]{32,}/)?.[0], jig.token);
    ctx.mark('token-end');

    const s = await ctx.screen();
    const p = s.page;
    await s.startRecording();
    await s.goto(`${BASE}/`);
    ctx.mark('ui-open');
    await ui.loginDialog(p).waitFor({ state: 'visible' });
    checks.ok('UI asks to sign in before showing anything', await ui.loginDialog(p).isVisible());
    ctx.mark('login-box', { box: await s.box(ui.loginDialog(p)) });
    await ctx.sleep(1200);
    await s.paste(ui.tokenField(p), jig.token);
    await s.click(ui.signIn(p));
    await waitUntil('the UI to connect to /events', async () => /Live/.test(await ui.connection(p).innerText()));
    ctx.mark('signed-in');
    checks.ok('sign-in dialog closed', !(await ui.loginDialog(p).isVisible()));

    const status = await jig.get('/status');
    checks.ok('/status: agent capability check passed', status.capabilities.agent.tool_calling && status.capabilities.agent.structured_output, status.capabilities);
    await waitUntil('the status panel to show the capability check', async () => /tool calling passed/.test(await ui.statusRegion(p).innerText()));
    checks.ok('status panel shows the capability check', /structured output passed/.test(await ui.statusRegion(p).innerText()));
    const capsRow = union(await s.box(ui.statusTerm(p, 'Capability check')), await s.box(ui.statusValue(p, /tool calling passed/)));
    ctx.mark('status-box', { box: capsRow, panel: await s.box(ui.statusRegion(p)) });
    await s.moveTo(ui.statusValue(p, /tool calling passed/), { ms: 800 });
    await ctx.sleep(1500);
    await s.click(ui.statusRefresh(p));
    await ctx.sleep(1200);
    ctx.mark('status-shown');

    const idle = await avatarState(p);
    checks.ok('avatar is idle before the first chat', idle.state === 'idle', idle);
    ctx.mark('avatar-box', { box: await s.box(ui.avatarRegion(p)) });
    await s.type(ui.chatInput(p), FIRST_MESSAGE);
    await followChat(ctx, s, { label: 'first-chat', send: () => s.press('Enter') });
    ctx.mark('chat-reply-box', { box: await s.box(ui.jigReplies(p).last()) });
    await ctx.sleep(4500);
    const after = await avatarState(p);
    checks.ok('avatar returns to idle after the chat', after.state === 'idle', after);
    ctx.mark('end');
    await s.stopRecording();
  },

  edit(m) {
    const k = marksOf(m);
    const health = k.cmd('host', 'jig health');
    const serve = k.cmd('host', 'jig serve');
    const token = k.cmd('token', 'jig token show');
    const hasCompose = m.notes.some((n) => n.text.startsWith('Compose deployment found'));
    const C1 = '1 · Check your model';
    const C2 = '2 · Start Jig';
    const C3 = '3 · Sign in';
    const C4 = '4 · First chat';
    const healthTo = Math.min(health.end + 2500, serve.start - 300);
    const serveTo = k.t('serve-ready') + 2500;
    const chatFrom = k.t('avatar-box');
    const chatTo = k.t('first-chat-done') + 4200;
    return {
      shots: [
        { type: 'card', dur: 3.6, card: { kicker: 'Jig · demo', title: 'Quick start', sub: 'From a fresh install to a first chat, on your own machine.', avatar: { state: 'idle' } } },
        { type: 'term', session: 'host', title: 'Windows PowerShell', chapter: C1, from: health.start - 400, to: healthTo, speed: fit(health.start, healthTo, 13),
          captions: [
            { at: health.start, dur: 5.5, text: '`jig health` checks the model server you configured.' },
            { at: health.enter + 1500, dur: 6.5, text: 'It runs *real* probes: a tool call, a JSON-schema answer and an image.' },
          ] },
        { type: 'term', session: 'host', title: 'Windows PowerShell', chapter: C2, from: serve.start - 300, to: serveTo, speed: fit(serve.start, serveTo, 10),
          captions: [
            { at: serve.start, dur: 5, text: '`jig serve` starts the always-on agent and its API, on 127.0.0.1 only.' },
            { at: k.t('serve-ready') - 3000, dur: 5, text: 'Start-up repeats the capability check. If it failed, Jig would refuse to start.' },
          ] },
        { type: 'term', session: 'token', title: 'Windows PowerShell · second tab', chapter: C3, from: token.start - 300, to: token.end + 2600,
          captions: [{ at: token.start, dur: 6, text: '`jig token show` prints the API token (*blanked out* in this video).' }] },
        { type: 'screen', chapter: C3, from: k.t('ui-open'), to: k.t('signed-in') + 1200,
          crops: [{ at: k.t('ui-open'), box: cropAround(k.box('login-box'), 16 / 9, { pad: 140 }), ease: 0.01 },
            { at: k.t('signed-in') + 1100, box: FULL, ease: 0.8 }],
          captions: [{ at: k.t('ui-open'), text: 'The web UI asks for the token once and swaps it for a session cookie.' }] },
        { type: 'screen', chapter: C3, from: k.t('signed-in') + 1200, to: k.t('status-shown'),
          rings: [{ from: k.t('signed-in') + 1800, to: k.t('status-shown'), box: k.box('status-box') }],
          captions: [{ at: k.t('signed-in') + 1200, text: 'The status panel shows the endpoint and the capability check that passed.' }] },
        { type: 'screen', chapter: C4, from: chatFrom, to: chatTo, speed: fit(chatFrom, chatTo, 20),
          rings: [{ from: k.t('first-chat-sent'), to: chatTo - 500, box: k.box('avatar-box') }],
          sfx: k.has('first-chat-success') ? [{ at: k.t('first-chat-success'), kind: 'success' }] : [],
          captions: [
            { at: chatFrom, dur: 3.5, text: 'Now a first message.' },
            { at: k.t('first-chat-thinking'), dur: 3, text: 'The avatar follows the real event stream: *thinking*…' },
            { at: k.t('first-chat-talking'), dur: 4, text: '…*talking* while the reply streams in…' },
            ...(k.has('first-chat-success') ? [{ at: k.t('first-chat-success'), dur: 3.5, text: '…and a little *success* when the run is done.' }] : []),
          ] },
        { type: 'card', dur: 4.2, card: { title: '*Jig*', sub: 'Your local, always-on agent. Open source, Apache-2.0.',
          lines: hasCompose ? ['Host install, shown here', 'Docker Compose deployment'] : ['Host install, shown here', 'Docker Compose deployment: in development'],
          avatar: { state: 'idle' } } },
      ],
    };
  },
};
