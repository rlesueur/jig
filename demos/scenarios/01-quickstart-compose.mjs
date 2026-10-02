/*
 * Demo 1, Docker Compose variant. The documented Windows quick start, typed for real in a checkout of
 * the commit under test: create the vault key, `docker compose up -d`, `docker compose exec jig jig token show`,
 * then sign in to the containerised Jig and chat with it. Its own project name, host port and image tags
 * (in .env, shown on camera) keep it apart from any other Compose project on this machine.
 * The images are built from the same checkout first (`docker compose build`, off camera).
 */
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { cropAround, fit, FULL, marksOf, until } from '../lib/edit.mjs';
import { BASE } from '../lib/jig.mjs';
import { ui } from '../lib/ui-map.mjs';
import { PORTS, snapshotDir, waitUntil } from '../lib/util.mjs';
import { followChat } from './common.mjs';
import { union } from './common-ui.mjs';

const PROJECT = 'jig-demo';
const KEY1 = '$b = [byte[]]::new(32); [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)';
const KEY2 = '[IO.File]::WriteAllText("$PWD\\secrets\\jig_vault_key", [Convert]::ToBase64String($b))';
const UP = 'docker compose up -d';
const TOKEN = 'docker compose exec jig jig token show';
const RESTART = "docker inspect --format '{{.Name}}: restart {{.HostConfig.RestartPolicy.Name}}' $(docker compose ps -q)";
const FIRST_MESSAGE = 'Hi Jig! In one short sentence, what can you help me with?';

function docker(args, cwd, timeoutMs = 1800000) {
  const r = spawnSync('docker', args, { cwd, encoding: 'utf8', windowsHide: true, timeout: timeoutMs, maxBuffer: 64 * 1024 * 1024 });
  if (r.status !== 0) throw new Error(`docker ${args.join(' ')} failed (${r.status}): ${(r.stderr || r.stdout || '').slice(-2000)}`);
  return r.stdout;
}

export default {
  title: 'Quick start with Docker Compose',

  async capture(ctx) {
    const { jig, checks } = ctx;
    const src = snapshotDir();
    const sha = path.basename(src).replace(/^src-/, '');
    if (!existsSync(path.join(src, 'compose.yaml'))) throw Object.assign(new Error(`compose.yaml is not in commit ${sha}`), { pending: true });
    const projects = JSON.parse(docker(['compose', 'ls', '--all', '--format', 'json'], src) || '[]');
    if (projects.some((x) => x.Name === PROJECT)) throw new Error(`a Compose project named ${PROJECT} already exists; not touching it`);

    /* a fresh checkout of the commit under test, with this demo's own .env */
    const dir = path.join(jig.root, 'jig');
    cpSync(src, dir, { recursive: true });
    writeFileSync(path.join(dir, '.env'), [
      `COMPOSE_PROJECT_NAME=${PROJECT}`, `JIG_HOST_PORT=${PORTS.jig}`,
      `JIG_IMAGE=${PROJECT}/jig:${sha}`, `JIG_SANDBOX_IMAGE=${PROJECT}/jig-sandbox:${sha}`, '',
    ].join('\n'));
    ctx.onCleanup('docker compose down -v (this demo\'s project only)', async () => {
      docker(['compose', 'down', '-v', '--remove-orphans'], dir, 300000);
    });
    ctx.note(`building ${PROJECT}/jig:${sha} and ${PROJECT}/jig-sandbox:${sha} from the checkout (off camera)`);
    docker(['compose', 'build'], dir);
    checks.ok('images built from the commit under test', true);

    const sh = await ctx.term('host', { cwd: dir, title: 'Windows PowerShell · jig checkout' });
    await sh.typeCommand('Get-Content .env');
    await sh.typeCommand(KEY1, { charMs: 24 });
    await sh.typeCommand(KEY2, { charMs: 24 });
    checks.ok('the vault key file exists', existsSync(path.join(dir, 'secrets', 'jig_vault_key')));
    const up = await sh.typeCommand(UP, { timeoutMs: 900000 });
    checks.ok('docker compose up -d started the services', /Started|Running|Healthy/.test(up.output), up.output.slice(-1500));
    await waitUntil('the containerised Jig to answer /health', async () => {
      try { const r = await fetch(`${BASE}/health`); return r.ok && (await r.json()).status === 'ok'; } catch { return false; }
    }, { timeoutMs: 400000, intervalMs: 1000 });
    ctx.mark('healthy');
    const tok = await sh.typeCommand(TOKEN);
    const token = tok.output.match(/[A-Za-z0-9_-]{32,}/)?.[0];
    await jig.attach(token);
    checks.ok('the token from the container works against the published port', true);

    const s = await ctx.screen();
    const p = s.page;
    await s.startRecording();
    await s.goto(`${BASE}/`);
    ctx.mark('ui-open');
    await ui.loginDialog(p).waitFor({ state: 'visible' });
    ctx.mark('login-box', { box: await s.box(ui.loginDialog(p)) });
    await ctx.sleep(1000);
    await s.paste(ui.tokenField(p), token);
    await s.click(ui.signIn(p));
    await waitUntil('the UI to connect to /events', async () => /Live/.test(await ui.connection(p).innerText()));
    ctx.mark('signed-in');
    await waitUntil('the status panel to show the capability check', async () => /tool calling passed/.test(await ui.statusRegion(p).innerText()));
    const status = await jig.get('/status');
    checks.ok('/status: the container passed the capability check', status.capabilities.agent.tool_calling && status.capabilities.agent.structured_output, status.capabilities);
    ctx.mark('status-box', { box: union(await s.box(ui.statusTerm(p, 'Model endpoint')), await s.box(ui.statusValue(p, /tool calling passed/))) });
    await ctx.sleep(2500);
    ctx.mark('avatar-box', { box: await s.box(ui.avatarRegion(p)) });
    await s.type(ui.chatInput(p), FIRST_MESSAGE);
    await followChat(ctx, s, { label: 'first-chat', send: () => s.press('Enter') });
    await ctx.sleep(4000);
    await s.stopRecording();

    const rs = await sh.typeCommand(RESTART);
    checks.ok('every service restarts unless stopped', (rs.output.match(/restart unless-stopped/g) || []).length >= 3, rs.output);
  },

  edit(m) {
    const k = marksOf(m);
    const env = k.cmd('host', 'Get-Content .env');
    const key1 = k.cmd('host', KEY1);
    const key2 = k.cmd('host', KEY2);
    const up = k.cmd('host', UP);
    const tok = k.cmd('host', TOKEN);
    const rs = k.cmd('host', RESTART);
    const T = 'Windows PowerShell · jig checkout';
    const C1 = '1 · Configure';
    const C2 = '2 · Start it';
    const C3 = '3 · Sign in';
    const C4 = '4 · First chat';
    const chatFrom = k.t('avatar-box');
    const chatTo = k.t('first-chat-done') + 3800;
    return {
      shots: [
        { type: 'card', dur: 3.6, card: { kicker: 'Jig · demo', title: 'Quick start with Docker', sub: 'Jig and its sandbox in containers, the model on your own machine.', avatar: { state: 'idle' } } },
        { type: 'term', session: 'host', title: T, chapter: C1, from: env.start - 300, to: until(key2.end + 1500, up), speed: fit(env.start, key2.end + 1500, 14),
          captions: [
            { at: env.start, dur: 5.5, text: 'Every setting is optional. This demo picks its own project name and port.' },
            { at: key1.start, text: 'Once: create the key that encrypts Jig’s vault.' },
          ] },
        { type: 'term', session: 'host', title: T, chapter: C2, from: up.start - 300, to: until(up.end + 1500, tok), speed: fit(up.start, up.end + 1500, 10),
          captions: [{ at: up.start, text: '`docker compose up -d`: Jig, plus a sandbox with no route to the internet except through Jig.' }] },
        { type: 'term', session: 'host', title: T, chapter: C3, from: tok.start - 300, to: tok.end + 2200,
          captions: [{ at: tok.start, text: 'Ask the container for the API token (*blanked out* here).' }] },
        { type: 'screen', chapter: C3, from: k.t('ui-open'), to: k.t('status-box') + 2400,
          crops: [{ at: k.t('ui-open'), box: cropAround(k.box('login-box'), 16 / 9, { pad: 140 }), ease: 0.01 }, { at: k.t('signed-in') + 600, box: FULL, ease: 0.8 }],
          rings: [{ from: k.t('status-box'), to: k.t('status-box') + 2400, box: k.box('status-box') }],
          captions: [
            { at: k.t('ui-open'), dur: 3, text: 'Sign in on the published port, loopback only.' },
            { at: k.t('signed-in') + 600, text: 'The container reaches the model on the host, and its capability check passed.' },
          ] },
        { type: 'screen', chapter: C4, from: chatFrom, to: chatTo, speed: fit(chatFrom, chatTo, 16),
          sfx: k.has('first-chat-success') ? [{ at: k.t('first-chat-success'), kind: 'success' }] : [],
          captions: [{ at: chatFrom, text: 'Same Jig, same UI, now in a container.' }] },
        { type: 'term', session: 'host', title: T, chapter: C4, from: rs.start - 300, to: rs.end + 3000,
          captions: [{ at: rs.start, text: 'Every service is `restart: unless-stopped`, so it comes back after a reboot.' }] },
        { type: 'card', dur: 4, card: { title: '*Jig*', sub: 'Your local, always-on agent. Open source, Apache-2.0.', avatar: { state: 'idle' } } },
      ],
    };
  },
};
