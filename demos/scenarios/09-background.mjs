/*
 * Launch demo: Background. A longer job given in "What Jig's up to": Jig plans it into tasks and works
 * through them in the background (the avatar dims while it reads), the strip under the conversation says
 * what it is doing, the running task is paused and resumed from the drawer, and the result arrives.
 *
 * Capability checks: the plan has a read-only research task, background browsing showed, pause and resume
 * really stopped and restarted the task, the goal finished, and the dates in the result match GOV.UK's own
 * bank-holiday data (fetched independently by this run) and the last Sunday of October.
 */
import { cropAround, fit, FULL, marksOf } from '../lib/edit.mjs';
import { avatarState, ui } from '../lib/ui-map.mjs';
import { waitUntil } from '../lib/util.mjs';
import { checkAvatarMirrors, startAvatarMonitor } from './common.mjs';
import { signIn, union } from './common-ui.mjs';

const TITLE = 'Dates for the diary';
const GOAL = 'Using the official GOV.UK pages, find the next three bank holidays in England and Wales and the next three in '
  + 'Scotland (https://www.gov.uk/bank-holidays.json), and when the clocks next go back '
  + '(https://www.gov.uk/when-do-the-clocks-change). Then write me a short plain-English note of the dates.';
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

/** The next n bank holidays after today in a GOV.UK division, as "25 December" style strings. */
function nextHolidays(data, division, n) {
  const today = new Date().toISOString().slice(0, 10);
  return data[division].events.filter((e) => e.date > today).slice(0, n)
    .map((e) => { const [, m, d] = e.date.split('-').map(Number); return { date: e.date, words: `${d} ${MONTHS[m - 1]}`, title: e.title }; });
}

/** The last Sunday of October this year (or next, if it has passed): when the UK clocks go back. */
function clocksGoBack() {
  const now = new Date();
  for (const y of [now.getFullYear(), now.getFullYear() + 1]) {
    const d = new Date(Date.UTC(y, 9, 31));
    d.setUTCDate(31 - d.getUTCDay());
    if (d > now) return `${d.getUTCDate()} October`;
  }
  throw new Error('unreachable');
}

export default {
  title: 'Background',
  formats: ['landscape'],
  theme: 'jig-light',
  scheme: 'light',

  async capture(ctx) {
    const { jig, checks } = ctx;
    await jig.start();
    const s = await ctx.screen();
    const p = s.page;
    await signIn(ctx, s);
    await startAvatarMonitor(p);
    await s.startRecording();
    await ctx.sleep(1200);
    ctx.mark('start', { avatar: await s.box(ui.avatarRegion(p)), doing: await s.box(ui.doing(p)) });

    /* the longer job, given in "What Jig's up to" */
    await s.click(ui.doingOpen(p));
    await ui.activity(p).waitFor({ state: 'visible' });
    ctx.mark('drawer-open');
    await s.type(ui.goalDescription(p), GOAL, { charMs: 11 });
    await s.type(ui.goalTitle(p), TITLE, { charMs: 40 });
    ctx.mark('goal-typed', { box: union(await s.box(ui.goalDescription(p)), await s.box(ui.createGoal(p))) });
    const since = ctx.mark('goal-submit').t;
    await s.click(ui.createGoal(p));
    const planned = await jig.events.waitFor('the goal to be planned', (e) => e.type === 'goal.status' && e.data.status === 'active' && e.data.plan, { since, timeoutMs: 300000 });
    ctx.mark('planned', { at: planned.t });
    const plan = planned.data.plan;
    ctx.note(`plan: ${plan.tasks.map((t) => `[${t.mode}] ${t.title}`).join(' | ')}`);
    checks.ok('the look-ups are planned as read-only research', plan.tasks.some((t) => t.mode === 'research'), plan.tasks);

    /* the first task starts; back to the main screen, where the strip and the dimmed avatar say what's happening */
    await jig.events.waitFor('the first task to start', (e) => e.type === 'task.status' && e.data.status === 'running', { since, timeoutMs: 120000 });
    await ctx.sleep(1800);
    ctx.mark('plan-shown', { box: await s.box(ui.doingList(p)) });
    await ctx.sleep(1500);
    await s.click(ui.activityClose(p));
    const bg = await jig.events.waitFor('dimmed background browsing', (e) => e.type === 'avatar.state' && e.data.state === 'working'
      && e.data.background === true, { since, timeoutMs: 120000 });
    ctx.mark('bg-browsing', { at: bg.t });
    await waitUntil('the strip to say what the task is doing', async () => /reading|working|checking/i.test(await ui.doingText(p).textContent()), { timeoutMs: 60000 });
    ctx.mark('strip', { box: union(await s.box(ui.doing(p)), await s.box(ui.avatarRegion(p))), text: await ui.doingText(p).textContent() });
    await ctx.sleep(3500);

    /* pause a task that is running right now (the plan differs from run to run), then resume it */
    await s.click(ui.doingOpen(p));
    await ui.activity(p).waitFor({ state: 'visible' });
    const runningNow = () => {
      const last = new Map();
      for (const e of jig.events.events) if (e.type === 'task.status' && e.t >= since) last.set(e.data.task_id, e.data);
      return [...last.values()].filter((t) => t.status === 'running');
    };
    const pick = await waitUntil('a task to be running', () => runningNow().at(-1), { timeoutMs: 120000 });
    const task = { id: pick.task_id, title: pick.title };
    ctx.note(`pausing task ${task.id} "${task.title}"`);
    const pause = ui.pauseTask(p, task.title);
    await pause.waitFor({ state: 'visible' });
    ctx.mark('pause', { box: await s.box(ui.doingItem(p, task.title)) });
    await s.click(pause);
    const paused = await jig.events.waitFor('the task to pause', (e) => e.type === 'task.status' && e.data.task_id === task.id && e.data.status === 'paused', { since, timeoutMs: 60000 });
    ctx.mark('paused', { at: paused.t });
    const resume = ui.resumeTask(p, task.title);
    await resume.waitFor({ state: 'visible' });
    checks.equal('pausing really paused the task', (await jig.get(`/tasks/${task.id}`)).status, 'paused');
    ctx.note(`avatar while paused: ${(await avatarState(p)).state}; other tasks running: ${runningNow().length}`);
    await ctx.sleep(1200);
    ctx.mark('paused-shown', { box: await s.box(ui.doingItem(p, task.title)) });
    await ctx.sleep(3500);
    const resumedAt = ctx.mark('resume').t;
    await s.click(resume);
    await jig.events.waitFor('the task to run again', (e) => e.type === 'task.status' && e.data.task_id === task.id && e.data.status === 'running', { since: resumedAt, timeoutMs: 60000 });
    ctx.mark('resumed');
    checks.ok('resuming the task set it running again', true);
    await ctx.sleep(2500);
    await s.click(ui.activityClose(p));

    /* wait for the result */
    const done = await jig.events.waitFor('the goal to finish', (e) => e.type === 'goal.status' && ['done', 'failed', 'cancelled'].includes(e.data.status), { since, timeoutMs: 900000 });
    checks.equal('the goal finished', done.data.status, 'done');
    const success = jig.events.avatarStates(since).find((x) => x.state === 'success');
    checks.ok('the avatar showed success', Boolean(success));
    ctx.mark('success', { at: success.t });
    await ctx.sleep(3000);
    await checkAvatarMirrors(ctx, p, { since, label: 'background' });

    /* the result, in the drawer */
    await s.click(ui.doingOpen(p));
    await ui.activity(p).waitFor({ state: 'visible' });
    await ctx.sleep(1200);
    ctx.mark('recent', { box: await s.box(ui.doingList(p)) });
    await ctx.sleep(1500);
    await s.click(ui.activityMoreSummary(p));
    const result = ui.goalResult(p, TITLE);
    await result.waitFor({ state: 'visible' });
    await s.click(result);
    await ctx.sleep(600);
    const item = ui.goalResultDetails(p, TITLE);
    await item.scrollIntoViewIfNeeded();
    ctx.mark('result', { box: await s.box(item) });
    await s.moveTo(item, { ms: 900 });
    await ctx.sleep(5000);
    ctx.mark('result-end');
    await s.stopRecording();

    /* check the dates against GOV.UK's own data */
    const goal = (await jig.get('/goals')).find((g) => g.title === TITLE);
    const text = `${goal.result || ''}`;
    const r = await fetch('https://www.gov.uk/bank-holidays.json');
    checks.ok('fetched GOV.UK bank holidays independently', r.ok, r.status);
    const data = await r.json();
    const ew = nextHolidays(data, 'england-and-wales', 3);
    const sc = nextHolidays(data, 'scotland', 3);
    const back = clocksGoBack();
    ctx.note(`GOV.UK: England and Wales ${ew.map((x) => x.words).join(', ')}; Scotland ${sc.map((x) => x.words).join(', ')}; clocks go back ${back}`);
    /* "25 December", "25th December" or "Fri 25 Dec" */
    const has = (words) => {
      const [day, month] = words.split(' ');
      return new RegExp(`\\b${day}(st|nd|rd|th)?\\s+${month.slice(0, 3)}(${month.slice(3)})?\\b`, 'i').test(text);
    };
    checks.ok('the result has the next three England and Wales bank holidays', ew.every((x) => has(x.words)), { expected: ew, text });
    checks.ok('the result has the next three Scottish bank holidays', sc.every((x) => has(x.words)), { expected: sc, text });
    checks.ok(`the result has the clocks going back on ${back}`, has(back), text);
  },

  edit(m) {
    const k = marksOf(m);
    const C1 = 'A longer job';
    const C2 = 'In the background';
    const C3 = 'Pause and resume';
    const C4 = 'Done';
    const zoom = (id, pad) => cropAround(k.box(id), 16 / 9, { pad });
    /* the result itself, from its top edge, not the goal's summary line above it */
    const fromTop = (id, pad) => {
      const c = zoom(id, pad);
      return { ...c, y: Math.max(0, Math.min(k.box(id).y - 8, 1080 - c.h)) };
    };
    return {
      shots: [
        { type: 'card', dur: 3.4, card: { kicker: 'Jig · real run', title: 'Jobs that run *in the background*',
          sub: 'Hand Jig a longer job, get on with your day, and check in whenever you like.', avatar: { state: 'working', task: 'browsing', background: true } } },
        { type: 'screen', chapter: C1, from: k.t('drawer-open') - 600, to: k.t('goal-submit') + 800, speed: fit(k.t('drawer-open') - 600, k.t('goal-submit') + 800, 7),
          captions: [{ at: k.t('drawer-open') - 600, text: 'A longer job: the next bank holidays and when the clocks go back, from GOV.UK.' }] },
        { type: 'screen', chapter: C1, from: k.t('goal-submit') + 800, to: k.t('plan-shown') + 2500, speed: fit(k.t('goal-submit') + 800, k.t('plan-shown') + 2500, 5),
          rings: [{ from: k.t('plan-shown') - 400, to: k.t('plan-shown') + 2500, box: k.box('plan-shown') }],
          captions: [{ at: k.t('goal-submit') + 800, text: 'Jig plans the steps: *read-only* research first, then the note.' }] },
        { type: 'screen', chapter: C2, from: k.t('plan-shown') + 2500, to: k.t('strip') + 3200, speed: fit(k.t('plan-shown') + 2500, k.t('strip') + 3200, 6),
          crops: [{ at: k.t('plan-shown') + 2500, box: FULL, ease: 0.01 }, { at: k.t('strip') - 400, box: zoom('strip', 120), ease: 0.8 }],
          captions: [{ at: k.t('plan-shown') + 2500, text: 'It works in the background. The avatar *dims* while it reads, and the strip says what it is up to.' }] },
        { type: 'screen', chapter: C3, from: k.t('strip') + 3200, to: k.t('resumed') + 1800, speed: fit(k.t('strip') + 3200, k.t('resumed') + 1800, 9),
          rings: [{ from: k.t('pause') - 300, to: k.t('paused') + 600, box: k.box('pause') }, { from: k.t('paused-shown'), to: k.t('resumed') + 1800, box: k.box('paused-shown') }],
          captions: [
            { at: k.t('strip') + 3200, text: 'Need it to stop for a moment? *Pause* it.' },
            { at: k.t('paused-shown'), text: 'Paused, with its place saved. *Resume*, and it carries on.' },
          ] },
        { type: 'screen', chapter: C4, from: k.t('resumed') + 1800, to: k.t('success') + 2800, speed: fit(k.t('resumed') + 1800, k.t('success') + 2800, 10),
          sfx: [{ at: k.t('success'), kind: 'success' }],
          captions: [{ at: k.t('resumed') + 1800, text: 'Jig finishes the research and writes the note.' }] },
        { type: 'screen', chapter: C4, from: k.t('success') + 2800, to: k.t('result-end'), speed: fit(k.t('success') + 2800, k.t('result-end'), 9),
          crops: [{ at: k.t('success') + 2800, box: FULL, ease: 0.01 }, { at: k.t('result') - 200, box: fromTop('result', 60), ease: 0.7 }],
          captions: [{ at: k.t('success') + 2800, text: 'The result. Every date in it matches GOV.UK’s own data.' }] },
        { type: 'card', dur: 3.8, card: { title: '*Jig*', sub: 'Your local, always-on agent. Open source.',
          lines: ['Works through longer jobs while you get on with yours'], avatar: { state: 'dance' } } },
      ],
    };
  },
};
