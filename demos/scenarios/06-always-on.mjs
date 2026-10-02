/*
 * Demo 6: Always on. The real `jig autostart` CLI in a real console (it asks on a real terminal):
 * status, enable (the full disclosure and the y/N question), then disable straight away and check.
 * The web UI's Status card shows the same entry. Autostart is left DISABLED, and that is asserted.
 * Compose deployments use `restart: unless-stopped` instead; the real compose.yaml is shown.
 */
import { spawnSync } from 'node:child_process';
import { fit, marksOf } from '../lib/edit.mjs';
import { ui } from '../lib/ui-map.mjs';
import { snapshotDir, venvBin, waitUntil } from '../lib/util.mjs';
import { signIn } from './common-ui.mjs';

const ENTRY = '\\Jig\\Jig Agent';

/** Whether Windows Task Scheduler has the task, asked directly (not through Jig). */
function taskRegistered() {
  const r = spawnSync('schtasks', ['/Query', '/TN', ENTRY], { encoding: 'utf8', windowsHide: true });
  return r.status === 0;
}

function statusJson(jig) {
  const r = spawnSync(venvBin('jig.exe'), ['autostart', 'status', '--json'], { env: jig.env(), encoding: 'utf8', windowsHide: true });
  if (r.status !== 0) throw new Error(`jig autostart status --json failed (${r.status}): ${r.stderr}`);
  return JSON.parse(r.stdout);
}

export default {
  title: 'Always on',

  async capture(ctx) {
    const { jig, checks } = ctx;
    if (process.platform !== 'win32') throw new Error('This recording uses Windows Task Scheduler');
    /* never touch an entry someone else registered */
    if (taskRegistered()) throw new Error(`${ENTRY} is already registered on this machine; not touching it`);
    checks.ok('autostart is not registered before the demo', statusJson(jig).registered === false);
    ctx.onCleanup('autostart left disabled', async () => {
      if (taskRegistered()) {
        const r = spawnSync(venvBin('jig.exe'), ['autostart', 'disable'], { env: jig.env(), encoding: 'utf8', windowsHide: true });
        if (r.status !== 0 || taskRegistered()) throw new Error(`could not remove ${ENTRY}: ${r.stderr}`);
        throw new Error(`${ENTRY} was still registered at the end and has now been removed`);
      }
    });

    await jig.start();
    const s = await ctx.screen();
    const p = s.page;
    await signIn(ctx, s);
    await s.startRecording();
    await waitUntil('the autostart row', async () => /^Off\b/.test((await ui.autostartValue(p).innerText()).trim()), { timeoutMs: 15000 });
    ctx.mark('ui-off', { box: await s.box(ui.autostartValue(p)) });
    await s.moveTo(ui.autostartButton(p), { ms: 900 });
    await ctx.sleep(2500);

    const sh = await ctx.term('host', { cwd: snapshotDir(), title: 'Windows PowerShell' });
    const st1 = await sh.typeCommand('jig autostart status');
    checks.ok('status says not registered', /not registered as \\Jig\\Jig Agent/.test(st1.output), st1.output);

    const en = await sh.typeCommand('jig autostart enable', { answers: [{ when: /\[y\/N\]\s*$/, send: 'y', readMs: 5000 }] });
    checks.ok('enable prints the disclosure first', /will register this[\s\S]*Command line:[\s\S]*Trigger:[\s\S]*Runs as:/.test(en.output), en.output);
    checks.ok('enable asks y/N on the terminal', /Register this so Jig starts automatically\? \[y\/N\]/.test(en.output));
    checks.ok('enable reports success', /Enabled: \\Jig\\Jig Agent/.test(en.output), en.output);
    checks.ok('Task Scheduler now has the task', taskRegistered());
    ctx.mark('enabled', { asked: en.answers[0].asked });

    const st2 = await sh.typeCommand('jig autostart status');
    checks.ok('status says REGISTERED', /REGISTERED as \\Jig\\Jig Agent/.test(st2.output), st2.output);

    const uiOnFrom = ctx.mark('ui-on-start').t;
    await s.click(ui.statusRefresh(p));
    await waitUntil('the UI to show autostart on', async () => /^On\b/.test((await ui.autostartValue(p).innerText()).trim()), { timeoutMs: 15000 });
    ctx.mark('ui-on', { box: await s.box(ui.autostartValue(p)), from: uiOnFrom });
    await ctx.sleep(2600);

    const dis = await sh.typeCommand('jig autostart disable');
    checks.ok('disable removes the task', /Removed:[\s\S]*Task Scheduler task \\Jig\\Jig Agent/.test(dis.output), dis.output);
    const st3 = await sh.typeCommand('jig autostart status');
    checks.ok('status says not registered again', /not registered as \\Jig\\Jig Agent/.test(st3.output), st3.output);
    checks.ok('Task Scheduler no longer has the task', !taskRegistered());
    checks.ok('jig autostart status --json: registered false', statusJson(jig).registered === false);

    const uiOffFrom = ctx.mark('ui-off2-start').t;
    await s.click(ui.statusRefresh(p));
    await waitUntil('the UI to show autostart off', async () => /^Off\b/.test((await ui.autostartValue(p).innerText()).trim()), { timeoutMs: 15000 });
    ctx.mark('ui-off2', { box: await s.box(ui.autostartValue(p)), from: uiOffFrom });
    await ctx.sleep(2600);
    await s.stopRecording();

    const cs = await sh.typeCommand("Select-String -Path compose.yaml -Pattern 'restart:'");
    checks.ok('compose.yaml restarts every service unless stopped', /restart: unless-stopped/.test(cs.output), cs.output);
  },

  edit(m) {
    const k = marksOf(m);
    const st1 = k.cmd('host', 'jig autostart status');
    const en = k.cmd('host', 'jig autostart enable');
    const all = k.term('host').commands.filter((c) => c.cmd === 'jig autostart status');
    const st2 = all[1];
    const st3 = all[2];
    const dis = k.cmd('host', 'jig autostart disable');
    const cs = k.cmd('host', "Select-String -Path compose.yaml -Pattern 'restart:'");
    const asked = en.answers[0].asked;
    /* a console shot may run on after its command, but never into the next one */
    const until = (t, next) => Math.min(t, next.start - 300);
    const C1 = '1 · Off by default';
    const C2 = '2 · Turn it on';
    const C3 = '3 · And off again';
    const C4 = '4 · In a container';
    const T = 'Windows PowerShell';
    return {
      shots: [
        { type: 'card', dur: 3.4, card: { kicker: 'Jig · demo', title: 'Always on', sub: 'Jig can start when you log in. Only if you say so.', avatar: { state: 'idle' } } },
        { type: 'screen', chapter: C1, from: k.t('ui-off') - 300, to: k.t('ui-off') + 2600,
          rings: [{ from: k.t('ui-off'), to: k.t('ui-off') + 2600, box: k.box('ui-off') }],
          captions: [{ at: k.t('ui-off') - 300, text: 'Autostart is *off* unless you turn it on.' }] },
        { type: 'term', session: 'host', title: T, chapter: C1, from: st1.start - 300, to: until(st1.end + 1800, en),
          captions: [{ at: st1.start, text: '`jig autostart status` asks Task Scheduler directly.' }] },
        { type: 'term', session: 'host', title: T, chapter: C2, from: en.start - 300, to: until(en.end + 1500, st2), speed: fit(en.start, until(en.end + 1500, st2), 14),
          sfx: [{ at: asked, kind: 'approval' }],
          captions: [
            { at: en.start, dur: 4, text: '`enable` first shows *exactly* what it will register…' },
            { at: asked, text: '…and asks. Nothing is registered until you answer *y*.' },
          ] },
        { type: 'term', session: 'host', title: T, chapter: C2, from: st2.start - 300, to: Math.min(st2.end + 1500, k.get('ui-on').from - 300),
          captions: [{ at: st2.start, text: 'Now Jig would start at your next logon.' }] },
        { type: 'screen', chapter: C2, from: k.get('ui-on').from - 300, to: k.t('ui-on') + 2400,
          rings: [{ from: k.t('ui-on'), to: k.t('ui-on') + 2400, box: k.box('ui-on') }],
          captions: [{ at: k.get('ui-on').from - 300, text: 'The web UI shows the same entry, and can turn it off.' }] },
        { type: 'term', session: 'host', title: T, chapter: C3, from: dis.start - 300, to: Math.min(st3.end + 1800, k.get('ui-off2').from - 300), speed: fit(dis.start, st3.end + 1800, 12),
          captions: [
            { at: dis.start, dur: 4, text: '`disable` removes the task again…' },
            { at: st3.start, text: '…and `status` confirms it: *not registered*.' },
          ] },
        { type: 'screen', chapter: C3, from: k.get('ui-off2').from - 300, to: k.t('ui-off2') + 2400,
          rings: [{ from: k.t('ui-off2'), to: k.t('ui-off2') + 2400, box: k.box('ui-off2') }],
          captions: [{ at: k.get('ui-off2').from - 300, text: 'Back to *off*. This machine is left that way.' }] },
        { type: 'term', session: 'host', title: `${T} · Jig repository`, chapter: C4, from: cs.start - 300, to: cs.end + 3200,
          captions: [{ at: cs.start, text: 'With Docker Compose, every service is `restart: unless-stopped` instead.' }] },
        { type: 'card', dur: 3.8, card: { title: '*Jig*', sub: 'Your local, always-on agent. Open source, Apache-2.0.', avatar: { state: 'idle' } } },
      ],
    };
  },
};
