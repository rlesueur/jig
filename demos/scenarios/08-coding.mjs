/*
 * Launch demo: Coding. A real small debugging job in Jig's container sandbox (Docker, jig-sandbox image): the
 * workspace holds a week of bakery sales (sales.csv), a report script with two genuine bugs (report.py) and
 * its tests (test_report.py). The tests are first run in a console, where they fail. Jig runs them in its
 * sandbox, finds the bugs, fixes report.py and runs them again until they pass, then runs the report.
 * Running code asks first (the usual setting), so each run shows its approval card with the exact command,
 * answered Yes in the conversation. Afterwards the tests are run again outside Jig, in the same console.
 *
 * Capability checks: report.py was changed, Jig ran the tests in the sandbox, its last test run passed (the exit
 * code from the run's step records), the reply claims no change that didn't happen, and the tests pass when run
 * independently.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cropAround, fit, FULL, marksOf } from '../lib/edit.mjs';
import { ui } from '../lib/ui-map.mjs';
import { checkClaims } from '../lib/claims.mjs';
import { answerCard, followChat, toolOutcomes } from './common.mjs';
import { backToChat, openSettings, signIn } from './common-ui.mjs';

const ASK = 'The tests for report.py in your workspace are failing. Run them in your sandbox with python3 -m unittest -v, '
  + 'find out why, fix report.py (standard library only) and run the tests again until they all pass. '
  + 'Then run report.py and show me the summary.';

/**
 * Each sandbox command the run asked for, with its exit code and output as Jig recorded them (the step's
 * structured result; a denied or failed call has no exit code, and says why in `error`).
 */
function commandsRun(run) {
  return toolOutcomes(run).filter((o) => o.tool === 'run_command' || o.tool === 'run_python').map((o) => ({
    tool: o.tool, command: String(o.args.command || o.args.code || ''), ok: o.ok,
    exit_code: o.result?.exit_code, stdout: o.result?.stdout || '', stderr: o.result?.stderr || '',
    error: o.ok ? null : o.errorType || o.status,
  }));
}

export default {
  title: 'Coding',
  formats: ['landscape'],
  theme: 'jig-dark',
  scheme: 'dark',
  config: 'demo-sandbox.toml',

  async capture(ctx) {
    const { jig, checks } = ctx;
    const seeded = jig.seed('coding');
    ctx.note(`workspace seeded with ${seeded.join(', ')}`);
    await jig.start();

    /* what's in the workspace, shown in a real console first */
    const sh = await ctx.term('files', { cwd: jig.workspace, title: 'Windows PowerShell · Jig workspace', cols: 110, rows: 32,
      env: { PYTHONUTF8: '1' } });
    const before = readFileSync(path.join(jig.workspace, 'report.py'), 'utf8');
    ctx.mark('files-start');
    await sh.typeCommand('Get-ChildItem -Name');
    const failing = await sh.typeCommand('python -m unittest', { expectCode: null });
    checks.ok('the tests fail before Jig starts', failing.code !== 0 && /Ran 4 tests[\s\S]*FAILED/.test(failing.output), failing.output.slice(-600));
    await ctx.sleep(2500);
    ctx.mark('files-end');

    const s = await ctx.screen();
    const p = s.page;
    await signIn(ctx, s);
    await openSettings(s, 'chat');
    await s.click(ui.showWorkingSwitch(p));
    await backToChat(s);
    await s.startRecording();
    await ctx.sleep(1200);
    ctx.mark('start', { avatar: await s.box(ui.avatarRegion(p)) });

    ctx.mark('ask-start');
    await s.type(ui.chatInput(p), ASK, { charMs: 12 });
    ctx.mark('ask-typed', { box: await s.box(ui.chatInput(p)) });
    let n = 0;
    const chat = await followChat(ctx, s, {
      label: 'code', send: () => s.press('Enter'), timeoutMs: 900000,
      onApproval: async (asked) => {
        n += 1;
        checks.ok(`approval ${n} is for running code in the sandbox`, ['run_command', 'run_python'].includes(asked.data.tool), asked.data.tool);
        ctx.mark(`ask-${n}`, { at: asked.t, tool: asked.data.tool });
        await answerCard(ctx, s, asked.data.tool, { label: `run${n}`, approve: true, readMs: n === 1 ? 3200 : 900 });
      },
    });
    ctx.mark('reply', { box: await s.box(ui.jigReplies(p).last()) });
    await ctx.sleep(1500);
    const log = ui.chatLog(p);
    const top = await log.evaluate((el) => el.scrollHeight);
    ctx.mark('read-start');
    await s.scroll(log, -Math.min(top, 3000), { steps: 26, stepMs: 60 });
    await ctx.sleep(1000);
    for (let i = 0; i < 4; i++) await s.scroll(log, 800, { steps: 28, stepMs: 110, after: 1300 });
    ctx.mark('read-end');
    await s.stopRecording();

    /* capability checks from the run's own record */
    const run = await jig.get(`/runs/${chat.runId}`);
    const cmds = commandsRun(run);
    const tests = cmds.filter((c) => /unittest/.test(c.command));
    ctx.note(`sandbox commands: ${cmds.map((c) => `[${c.exit_code ?? c.error ?? '?'}] ${c.command.slice(0, 80)}`).join(' | ')}`);
    const changed = readFileSync(path.join(jig.workspace, 'report.py'), 'utf8') !== before;
    checks.ok('Jig changed report.py', changed);
    checkClaims(ctx, 'code', chat.final, [{ kind: 'save', what: 'report.py was changed', about: /report\.py|\breport\b|\bscript\b/i,
      happened: changed, outcome: changed ? 'report.py in the workspace differs from the seeded one' : 'report.py is as seeded' }]);
    checks.ok('Jig ran the tests in its sandbox', tests.length >= 1, cmds.map((c) => c.command));
    checks.equal('Jig\'s last test run in the sandbox passed (exit code)', tests.at(-1)?.exit_code, 0);
    const firstFailed = tests.length > 1 && tests[0].exit_code !== 0;
    ctx.note(firstFailed ? `Jig's first test run failed (exit ${tests[0].exit_code}): ${(tests[0].stderr || tests[0].stdout || '').split('\n').filter((l) => /FAIL|Error|assert/i.test(l)).slice(0, 4).join(' / ')}`
      : 'Jig did not see a failing run itself (it fixed the code before running the tests)');
    ctx.mark('summary', { firstFailed, testRuns: tests.length, exitCodes: tests.map((t) => t.exit_code) });

    /* the tests, run again outside Jig, in the same console */
    ctx.mark('verify-start');
    const verify = await sh.typeCommand('python -m unittest -v', { expectCode: null });
    checks.ok('the tests pass when run outside Jig', verify.code === 0 && /Ran 4 tests[\s\S]*\bOK\b/.test(verify.output), verify.output.slice(-600));
    await ctx.sleep(2500);
    ctx.mark('verify-end');
  },

  edit(m) {
    const k = marksOf(m);
    const sum = k.get('summary');
    const asks = m.marks.filter((x) => /^ask-\d+$/.test(x.id)).length;
    const C1 = 'The job';
    const C2 = 'Jig codes';
    const C3 = 'It asks to run code';
    const C4 = 'Tested';
    const files = k.cmd('files', 'python -m unittest');
    const verify = k.cmd('files', 'python -m unittest -v');
    const firstRun = k.t('run1-answer');
    const shots = [
      { type: 'card', dur: 3.4, card: { kicker: 'Jig · real run', title: 'Real code, *really run*',
        sub: 'Jig runs the tests in a sandbox, finds the bugs, and fixes them.', avatar: { state: 'working', task: 'coding' } } },
      { type: 'term', session: 'files', title: 'Windows PowerShell · Jig workspace', chapter: C1, from: k.t('files-start'), to: files.end + 2200,
        speed: fit(k.t('files-start'), files.end + 2200, 4),
        captions: [{ at: k.t('files-start'), text: 'A bakery’s weekly sales report. Its tests *fail*.' }] },
      { type: 'screen', chapter: C1, from: k.t('ask-start'), to: k.t('code-sent') + 1200, speed: fit(k.t('ask-start'), k.t('code-sent') + 1200, 6),
        captions: [{ at: k.t('ask-start'), text: 'Ask Jig to find out why, and *fix it*.' }] },
      { type: 'screen', chapter: C2, from: k.t('code-sent') + 1200, to: k.t('ask-1') - 300, speed: fit(k.t('code-sent') + 1200, k.t('ask-1') - 300, 7),
        captions: [{ at: k.t('code-sent') + 1200, text: 'Jig reads the script, the tests and the data.' }] },
      { type: 'screen', chapter: C3, from: k.t('ask-1') - 300, to: firstRun + 1200, speed: 1, sfx: [{ at: k.t('ask-1'), kind: 'approval' }],
        rings: [{ from: k.t('run1-card'), to: firstRun + 600, box: k.box('run1-card') }],
        captions: [{ at: k.t('ask-1') - 300, text: 'Running code is an action, so Jig *asks first*, showing the exact command for its sandbox.' }] },
    ];
    const restFrom = firstRun + 1200;
    const restTo = k.t('code-done') + 1500;
    shots.push({ type: 'screen', chapter: C4, from: restFrom, to: restTo, speed: fit(restFrom, restTo, 14), sfx: [{ at: k.t('code-done'), kind: 'success' }],
      captions: sum.firstFailed
        ? [{ at: restFrom, text: `The tests *fail* in the sandbox too. Jig reads the errors, fixes the script and runs them again (${asks} runs, each one approved).` },
          { at: restFrom + (restTo - restFrom) * 0.7, text: 'All four tests *pass*, and Jig runs the report.' }]
        : [{ at: restFrom, text: `Jig fixes the script, the tests pass, and it runs the report (${asks} runs, each one approved).` }] });
    shots.push(
      { type: 'screen', chapter: C4, from: k.t('read-start'), to: k.t('read-end'), speed: fit(k.t('read-start'), k.t('read-end'), 10),
        captions: [{ at: k.t('read-start'), text: 'Jig says what was wrong, what it changed, and the result.' }] },
      { type: 'term', session: 'files', title: 'Windows PowerShell · Jig workspace', chapter: C4, from: verify.start - 400, to: verify.end + 2500,
        captions: [{ at: verify.start - 400, text: 'Checked outside Jig: the same tests, run again. *OK*.' }] },
      { type: 'card', dur: 3.8, card: { title: '*Jig*', sub: 'Your local, always-on agent. Open source.',
        lines: ['Code runs in a sandbox, and only with your OK'], avatar: { state: 'dance' } } },
    );
    return { shots };
  },
};
