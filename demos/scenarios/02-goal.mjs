/*
 * Demo 2: Give Jig a goal. Created in the real UI, planned by the real model into tasks; a read-only
 * research task runs in the background (dimmed browsing avatar); writing the result needs approval
 * (approval avatar, Sentinel verdict); approved in the inbox; success; then the real file and the
 * activity view. One custom rule, "ask before writing any file", is set up first and shown in the captions.
 */
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { cropAround, fit, FULL, marksOf } from '../lib/edit.mjs';
import { avatarState, ui } from '../lib/ui-map.mjs';
import { waitUntil } from '../lib/util.mjs';
import { checkAvatarMirrors, startAvatarMonitor } from './common.mjs';
import { openTab, signIn, union } from './common-ui.mjs';

const FILE = 'coffee-protocol.md';
const GOAL = 'Read RFC 2324 at https://www.rfc-editor.org/rfc/rfc2324.html (the April Fools\' coffee-pot protocol) '
  + `and save a plain-English summary of it, as five short bullet points, to ${FILE} in the workspace.`;
const TITLE = 'Summarise the coffee-pot protocol';

export default {
  title: 'Give Jig a goal',
  formats: ['landscape', 'portrait'],

  async capture(ctx) {
    const { jig, checks } = ctx;
    await jig.start();
    const rule = await jig.post('/rules', { tool: 'write_file', decision: 'ask', note: 'Ask me before writing any file' });
    checks.equal('custom rule write_file -> ask created', rule.decision, 'ask');

    const s = await ctx.screen();
    const p = s.page;
    await signIn(ctx, s);
    await startAvatarMonitor(p);
    await s.startRecording();
    await ctx.sleep(1200);
    ctx.mark('start', { avatar: await s.box(ui.avatarRegion(p)) });

    await openTab(s, 'Activity');
    ctx.mark('activity-open');
    await s.type(ui.goalDescription(p), GOAL, { charMs: 14 });
    await s.type(ui.goalTitle(p), TITLE, { charMs: 30 });
    ctx.mark('goal-typed', { box: union(await s.box(ui.goalDescription(p)), await s.box(ui.createGoal(p))) });
    const since = ctx.mark('goal-submit').t;
    await s.click(ui.createGoal(p));

    const planned = await jig.events.waitFor('the goal to be planned', (e) => e.type === 'goal.status' && e.data.status === 'active', { since, timeoutMs: 300000 });
    ctx.mark('planned', { at: planned.t });
    const plan = planned.data.plan;
    checks.ok('the planner produced 1 to 5 tasks', plan?.tasks?.length >= 1 && plan.tasks.length <= 5, plan);
    const modes = plan.tasks.map((t) => t.mode);
    ctx.note(`plan: ${plan.tasks.map((t) => `[${t.mode}] ${t.title}`).join(' | ')}`);
    await ctx.sleep(800);
    ctx.mark('plan-shown', { box: await s.box(ui.panel(p, 'Activity').getByText(TITLE, { exact: true }).first()) });

    /* the read-only research task: dimmed browsing while its web tool runs */
    let bg = null;
    if (modes.includes('research')) {
      bg = await jig.events.waitFor('dimmed background browsing', (e) => e.type === 'avatar.state' && e.data.state === 'working'
        && e.data.background === true, { since, timeoutMs: 400000 }).catch(() => null);
      if (bg) ctx.mark('bg-browsing', { at: bg.t });
    }
    checks.ok('the avatar showed background (dimmed) research', Boolean(bg), `plan modes: ${modes.join(', ')}`);

    const asked = await jig.events.waitFor('an approval request', (e) => e.type === 'approval.requested' && !e.data.resumed, { since, timeoutMs: 600000 });
    ctx.mark('approval-requested', { at: asked.t, tool: asked.data.tool });
    checks.equal('the approval is for write_file', asked.data.tool, 'write_file');
    await waitUntil('the page avatar to show approval', async () => (await avatarState(p)).state === 'approval', { timeoutMs: 8000 });
    checks.ok('page avatar is in the approval state', true);
    await ctx.sleep(1500);
    await openTab(s, 'Approvals');
    const approve = ui.approve(p, 'write_file');
    await approve.waitFor({ state: 'visible' });
    const pending = await jig.get('/approvals?status=pending');
    checks.equal('exactly one approval is pending', pending.length, 1);
    const ap = pending[0];
    checks.ok('the approval carries the Sentinel verdict', Boolean(ap.sentinel?.verdict), ap.sentinel);
    checks.ok('the approval lists the custom rule as a reason', ap.reasons.some((r) => r.rule === rule.id), ap.reasons);
    ctx.note(`Sentinel on write_file: ${ap.sentinel.verdict} (${ap.sentinel.risk}) - ${ap.sentinel.reason}`);
    const panelText = await ui.panel(p, 'Approvals').innerText();
    checks.ok('the inbox shows the Sentinel verdict and reason', panelText.includes(ap.sentinel.reason.slice(0, 40)), panelText.slice(0, 400));
    /* measured after the pointer has moved there, since hovering can scroll the page */
    await s.moveTo(approve, { ms: 900 });
    await ctx.sleep(300);
    ctx.mark('inbox', { box: union(await s.box(approve), await s.box(ui.deny(p, 'write_file'))), approve: await s.box(approve) });
    await ctx.sleep(2300);
    const approvedAt = ctx.mark('approve').t;
    await s.click(approve);

    const done = await jig.events.waitFor('the goal to finish', (e) => e.type === 'goal.status' && ['done', 'failed'].includes(e.data.status), { since: approvedAt, timeoutMs: 600000 });
    checks.equal('the goal finished successfully', done.data.status, 'done');
    const success = jig.events.avatarStates(approvedAt).find((x) => x.state === 'success');
    checks.ok('the avatar showed success', Boolean(success));
    ctx.mark('success', { at: success.t });
    await ctx.sleep(3800);
    await checkAvatarMirrors(ctx, p, { since, label: 'goal' });

    await openTab(s, 'Activity');
    await ctx.sleep(1000);
    ctx.mark('activity', { box: await s.box(ui.goalHeading(p)) });
    await s.moveTo(ui.runsTable(p), { ms: 1200 });
    await ctx.sleep(2500);
    ctx.mark('runs', { box: await s.box(ui.runsTable(p)) });
    await s.stopRecording();

    const file = path.join(jig.workspace, FILE);
    checks.ok(`${FILE} exists in the workspace`, existsSync(file), file);
    const text = readFileSync(file, 'utf8');
    checks.ok(`${FILE} has bullet points`, (text.match(/^\s*[-*•]\s+/gm) || []).length >= 3, text.slice(0, 300));
    const sh = await ctx.term('result', { cwd: jig.workspace, title: 'Windows PowerShell · Jig workspace' });
    ctx.mark('file-start');
    await sh.typeCommand('Get-ChildItem -Name');
    await sh.typeCommand(`Get-Content ${FILE}`);
    ctx.mark('file-end');
    const goal = (await jig.get('/goals'))[0];
    checks.equal('GET /goals reports the goal as done', goal.status, 'done');
  },

  edit(m, format) {
    const k = marksOf(m);
    const portrait = format === 'portrait';
    const aspect = portrait ? 1032 / 774 : 16 / 9;
    const C1 = '1 · Set a goal';
    const C2 = '2 · Jig plans and researches';
    const C3 = '3 · You approve';
    const C4 = '4 · Done';
    const zoom = (id, opts) => (portrait ? cropAround(k.box(id), aspect, opts) : FULL);
    const avatarCrop = portrait ? cropAround(k.get('start').avatar, aspect, { pad: 40 }) : FULL;
    const header = (title) => (portrait ? { kicker: 'Give Jig a goal', title } : null);
    const hasBg = k.has('bg-browsing');
    /* the answer buttons and what sits above them (the Sentinel's verdict and the reasons) */
    const inboxFrame = () => {
      const b = k.box('inbox');
      return cropAround({ x: b.x + b.w - 1150, y: b.y - 600, w: 1150, h: 600 + b.h }, aspect, { pad: 30 });
    };
    const planEnd = k.t('approval-requested') - 1500;
    const shots = [
      { type: 'card', dur: portrait ? 2.6 : 3.4, card: { kicker: 'Jig · demo', title: 'Give Jig a goal', sub: 'It plans, researches, asks before it acts, and finishes the job.', avatar: { state: 'thinking' } } },
      { type: 'screen', chapter: C1, header: header('Set a *goal*'), from: k.t('activity-open'), to: k.t('goal-submit') + 1200,
        speed: fit(k.t('activity-open'), k.t('goal-submit') + 1200, portrait ? 4 : 7),
        crops: [{ at: k.t('activity-open'), box: zoom('goal-typed', { pad: 80 }), ease: 0.01 }],
        captions: [{ at: k.t('activity-open'), text: 'A goal in plain words. One custom rule is set: *ask before writing any file*.' }] },
      { type: 'screen', chapter: C2, header: header('It *plans* the work'), from: k.t('goal-submit') + 1200, to: k.t('plan-shown') + 2500,
        speed: fit(k.t('goal-submit'), k.t('plan-shown') + 2500, portrait ? 4 : 6),
        crops: [{ at: k.t('goal-submit'), box: zoom('plan-shown', { pad: 260 }), ease: 0.01 }],
        captions: [{ at: k.t('goal-submit') + 1200, text: 'The model plans it into tasks: read-only research first, then the action.' }] },
    ];
    if (hasBg) {
      const bgFrom = Math.max(k.t('plan-shown') + 2500, k.t('bg-browsing') - 4000);
      shots.push({ type: 'screen', chapter: C2, header: header('Background *research*'), from: bgFrom, to: planEnd,
        speed: fit(bgFrom, planEnd, portrait ? 6 : 10),
        crops: [{ at: bgFrom, box: avatarCrop, ease: 0.01 }],
        rings: portrait ? [] : [{ from: k.t('bg-browsing') - 500, to: planEnd, box: k.get('start').avatar }],
        captions: [{ at: bgFrom, text: 'Research runs *read-only* in the background: the avatar dims while it browses.' }] });
    }
    shots.push(
      { type: 'screen', chapter: C3, header: header('It asks before it *acts*'), from: planEnd, to: k.t('approve') + 1500,
        speed: fit(planEnd, k.t('approve') + 1500, portrait ? 7 : 11),
        sfx: [{ at: k.t('approval-requested'), kind: 'approval' }],
        crops: portrait ? [{ at: planEnd, box: avatarCrop, ease: 0.01 }, { at: k.t('inbox'), box: inboxFrame(), ease: 0.8 }] : [],
        rings: portrait ? [] : [{ from: k.t('inbox'), to: k.t('approve'), box: k.box('inbox') }],
        captions: [
          { at: planEnd, dur: 3.5, text: 'Writing the file needs your approval, so Jig *pauses* and waits.' },
          { at: k.t('inbox'), text: 'The inbox shows why, and the *Sentinel*’s own verdict on the action.' },
        ] },
      { type: 'screen', chapter: C4, header: header('*Done*'), from: k.t('approve') + 1500, to: k.t('success') + 3600,
        speed: fit(k.t('approve') + 1500, k.t('success') + 3600, portrait ? 5 : 8),
        sfx: [{ at: k.t('success'), kind: 'success' }],
        crops: [{ at: k.t('approve'), box: avatarCrop, ease: 0.01 }],
        captions: [{ at: k.t('approve') + 1500, text: 'Approved. Jig writes the file and the goal is done.' }] },
    );
    if (!portrait) {
      const f = k.cmd('result', `Get-Content ${FILE}`);
      shots.push(
        { type: 'screen', chapter: C4, from: k.t('success') + 3600, to: k.t('runs'),
          rings: [{ from: k.t('runs') - 2600, to: k.t('runs'), box: k.box('runs') }],
          captions: [{ at: k.t('success') + 3600, text: 'Activity keeps the goal, its tasks and every run.' }] },
        { type: 'term', session: 'result', title: 'Windows PowerShell · Jig workspace', chapter: C4, from: k.t('file-start'), to: f.end + 2500,
          captions: [{ at: k.t('file-start'), text: 'And the result is a real file in Jig’s workspace.' }] },
      );
    }
    shots.push({ type: 'card', dur: portrait ? 3.2 : 3.8, card: { title: '*Jig*', sub: portrait ? 'Your local, always-on agent.' : 'Your local, always-on agent. Open source, Apache-2.0.',
      lines: portrait ? ['Open source · Apache-2.0'] : [], avatar: { state: 'success' } } });
    return { shots };
  },
};
