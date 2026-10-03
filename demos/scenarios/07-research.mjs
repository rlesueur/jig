/*
 * Launch demo: Research. A real question that needs several real web pages: Jig reads the official railcard
 * pages, compares three railcards for a family, cites where each fact came from and saves a short report.
 * File saves are set to "Ask me first" in Settings on camera, so the save shows its approval card, which is
 * approved in the conversation. The report is then shown as the real file in Jig's workspace.
 *
 * Capability checks: at least three pages read from railcard.co.uk, the reply and the file name all three
 * railcards and cite railcard.co.uk addresses, every cited address is a page Jig actually read (or where a redirect
 * it was shown took it), the save asked first and happened only after Yes, and the reply claims no save that
 * didn't happen. Pages and outcomes come from the run's record (GET /runs/<id>), not the content-free audit log.
 */
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { cropAround, fit, FULL, marksOf } from '../lib/edit.mjs';
import { ui } from '../lib/ui-map.mjs';
import { checkClaims } from '../lib/claims.mjs';
import { answerCard, followChat, happened, toolOutcomes } from './common.mjs';
import { backToChat, openSettings, signIn, union } from './common-ui.mjs';

const FILE = 'railcards.md';
const ASK = 'Compare the Family & Friends Railcard, the Two Together Railcard and the 16-25 Railcard for a family of two '
  + 'adults and two children who take the train a few times a year. Read the official pages on railcard.co.uk, then '
  + 'tell me the price, who can use it, the discount and the main restrictions of each, citing the web address each '
  + `fact came from. Then save a short report with the sources to ${FILE}.`;
const CARDS = [/Family (&|and) Friends/i, /Two Together/i, /16-25/];
/* the official site's host, exactly: railcards.co.uk is another site */
const onRailcard = (u) => {
  try { return /^(.+\.)?railcard\.co\.uk$/i.test(new URL(/^https?:\/\//i.test(u) ? u : `https://${u}`).hostname); } catch { return false; }
};
/* Windows PowerShell 5 reads files without a byte order mark as ANSI, which garbles £ and dashes. */
const SHOW_FILE = `Get-Content -Encoding UTF8 ${FILE}`;

export default {
  title: 'Research',
  formats: ['landscape', 'portrait'],
  theme: 'jig-light',
  scheme: 'light',

  async capture(ctx) {
    const { jig, checks } = ctx;
    await jig.start();
    const s = await ctx.screen();
    const p = s.page;
    await signIn(ctx, s);
    await s.startRecording();
    await ctx.sleep(1200);
    ctx.mark('start', { avatar: await s.box(ui.avatarRegion(p)) });

    /* Settings: saving files asks first; show Jig's working under replies */
    await openSettings(s, 'rules');
    const choice = ui.toolChoice(p, 'Save files in its workspace');
    await choice.scrollIntoViewIfNeeded();
    ctx.mark('rules', { box: await s.box(ui.toolChoiceRow(p, 'write_file')) });
    await s.select(choice, 'ask');
    await ui.toolChoiceSaved(p).filter({ hasText: 'Saved' }).waitFor({ state: 'visible' });
    checks.ok('Settings saved "Save files in its workspace: Ask me first"',
      (await jig.get('/rules')).some((r) => r.tool === 'write_file' && r.decision === 'ask' && r.enabled));
    ctx.mark('rules-saved', { box: union(await s.box(ui.toolChoiceRow(p, 'write_file')), await s.box(ui.toolChoiceSaved(p))) });
    await ctx.sleep(1800);
    await openSettings(s, 'chat');
    await s.click(ui.showWorkingSwitch(p));
    checks.ok('"Show Jig\'s working" is on', await ui.showWorkingSwitch(p).isChecked());
    await ctx.sleep(900);
    await backToChat(s);
    await ctx.sleep(600);

    /* the question */
    ctx.mark('ask-start');
    await s.type(ui.chatInput(p), ASK, { charMs: 12 });
    ctx.mark('ask-typed', { box: await s.box(ui.chatInput(p)) });
    let approvalAt = null;
    const chat = await followChat(ctx, s, {
      label: 'research', send: () => s.press('Enter'), timeoutMs: 600000,
      onApproval: async (asked) => {
        checks.equal('the only approval asked for is the file save', asked.data.tool, 'write_file');
        approvalAt = asked.t;
        ctx.mark('approval-asked', { at: asked.t });
        await answerCard(ctx, s, 'write_file', { label: 'save', approve: true });
      },
    });
    ctx.mark('reply', { box: await s.box(ui.jigReplies(p).last()) });
    await ctx.sleep(1500);

    /* read the reply from its start */
    const log = ui.chatLog(p);
    const top = await log.evaluate((el) => el.scrollHeight);
    ctx.mark('read-start');
    await s.scroll(log, -Math.min(top, 2600), { steps: 26, stepMs: 60 });
    await ctx.sleep(1200);
    await s.scroll(log, 900, { steps: 30, stepMs: 110, after: 1500 });
    await s.scroll(log, 900, { steps: 30, stepMs: 110, after: 1500 });
    await s.scroll(log, 1600, { steps: 30, stepMs: 110, after: 2500 });
    ctx.mark('read-end');
    await s.stopRecording();

    /* capability checks, from the run's own record (the audit log keeps only the sizes of addresses) */
    const run = await jig.get(`/runs/${chat.runId}`);
    const outcomes = toolOutcomes(run);
    const pages = outcomes.filter((o) => /^(web_fetch|browser_open)$/.test(o.tool));
    const norm = (u) => u.toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/[?#].*$/, '').replace(/\/+$/, '');
    const asked = (o) => String(o.args.url || '');
    /* where the page really came from: web_fetch gives the address its redirects ended at, browser_open the page's own */
    const landed = (o) => String((o.tool === 'web_fetch' ? o.result?.final_url : o.result?.url) || asked(o));
    const redirected = (o) => o.ok && norm(landed(o)) !== norm(asked(o));
    const read = pages.filter((o) => o.ok);
    ctx.note(`pages: ${pages.map((o) => `${asked(o)}${redirected(o) ? ` → ${landed(o)}` : ''} (${o.ok ? 'read' : o.errorType || o.status})`).join(' | ')}`);
    const railcardPages = new Set(read.map(landed).filter(onRailcard).map(norm));
    checks.ok('read at least three railcard.co.uk pages', railcardPages.size >= 3, pages.map((o) => `${landed(o)} ${o.ok ? 'read' : o.errorType}`));
    /* A citation counts only if it is a page Jig actually read (ignoring scheme, "www.", a trailing slash, query and
     * fragment): the address it asked for, or the one a redirect it was shown took it to. */
    const readSet = new Set(read.flatMap((o) => [asked(o), landed(o)]).map(norm));
    const cited = (t) => [...new Set((t.match(/https?:\/\/[^\s)\]>"'`]+/gi) || []).map((u) => u.replace(/[.,;:]+$/, ''))
      .filter(onRailcard))];
    const unread = (urls) => urls.filter((u) => !readSet.has(norm(u)));
    const replyCited = cited(chat.final);
    ctx.note(`reply cites: ${replyCited.join(' | ') || '(none)'}`);
    checks.ok('the reply names all three railcards', CARDS.every((re) => re.test(chat.final)), chat.final.slice(0, 600));
    checks.ok('the reply cites railcard.co.uk addresses', replyCited.length > 0, chat.final.slice(0, 600));
    checks.ok('every address the reply cites is a page Jig read', unread(replyCited).length === 0, unread(replyCited));
    checks.ok('the save waited for approval', Boolean(approvalAt));
    const file = path.join(jig.workspace, FILE);
    checks.ok(`${FILE} exists in the workspace`, existsSync(file), file);
    const text = readFileSync(file, 'utf8');
    checks.ok(`${FILE} covers all three railcards`, CARDS.every((re) => re.test(text)), text.slice(0, 400));
    const fileCited = cited(text);
    ctx.note(`${FILE}: ${text.length} characters; cites: ${fileCited.join(' | ') || '(none)'}`);
    checks.ok(`${FILE} lists railcard.co.uk sources`, fileCited.length > 0, text.slice(0, 400));
    checks.ok(`every address ${FILE} cites is a page Jig read`, unread(fileCited).length === 0, unread(fileCited));
    const saved = happened(outcomes, ['write_file'], (o) => path.basename(String(o.args.path || '')) === FILE);
    checkClaims(ctx, 'research', chat.final, [{ kind: 'save', what: `${FILE} was saved`, ...saved, happened: saved.happened && existsSync(file) }]);

    const sh = await ctx.term('result', { cwd: jig.workspace, title: 'Windows PowerShell · Jig workspace', cols: 120, rows: 34 });
    ctx.mark('file-start');
    await sh.typeCommand(SHOW_FILE);
    await ctx.sleep(2500);
    ctx.mark('file-end');
  },

  edit(m, format) {
    const k = marksOf(m);
    const portrait = format === 'portrait';
    const aspect = portrait ? 1032 / 774 : 16 / 9;
    const zoom = (box, pad = 80) => (portrait ? cropAround(box, aspect, { pad }) : FULL);
    const header = (title) => (portrait ? { kicker: 'Jig · research', title } : null);
    const C1 = 'Ask';
    const C2 = 'Jig researches';
    const C3 = 'It asks before saving';
    const C4 = 'The answer';
    const replyEnd = k.t('research-done') + 1200;
    const shots = [
      { type: 'card', dur: portrait ? 2.8 : 3.4, card: { kicker: 'Jig · real run', title: 'Research, with *sources*',
        sub: 'A real question, real web pages, a real report. Nothing staged.', avatar: { state: 'thinking' } } },
      { type: 'screen', chapter: 'Your rules', header: header('Your *rules*'), from: k.t('rules') - 600, to: k.t('rules-saved') + 1500,
        speed: 1, crops: [{ at: k.t('rules') - 600, box: zoom(k.box('rules-saved'), 160), ease: 0.01 }],
        rings: [{ from: k.t('rules-saved') - 200, to: k.t('rules-saved') + 1500, box: k.box('rules-saved') }],
        captions: [{ at: k.t('rules') - 600, text: 'First, one setting: saving files should *ask me first*.' }] },
      { type: 'screen', chapter: C1, header: header('Ask a *real question*'), from: k.t('ask-start'), to: k.t('research-sent') + 1500,
        speed: fit(k.t('ask-start'), k.t('research-sent') + 1500, portrait ? 5 : 7),
        crops: [{ at: k.t('ask-start'), box: zoom(k.box('ask-typed'), 120), ease: 0.01 }],
        captions: [{ at: k.t('ask-start'), text: 'Which railcard suits a family of four? Read the official pages and *cite them*.' }] },
      { type: 'screen', chapter: C2, header: header('It reads the *web*'), from: k.t('research-sent') + 1500, to: k.t('approval-asked') - 300,
        speed: fit(k.t('research-sent') + 1500, k.t('approval-asked') - 300, portrait ? 12 : 16),
        captions: [
          { at: k.t('research-sent') + 1500, text: 'Jig reads the railcard pages one by one. The avatar shows it *browsing*.' },
          { at: k.t('research-sent') + (k.t('approval-asked') - k.t('research-sent')) * 0.5, text: 'Each web request is checked by Jig’s *Sentinel* before it goes out.' },
        ] },
      { type: 'screen', chapter: C3, header: header('It *asks* first'), from: k.t('approval-asked') - 300, to: k.t('save-answer') + 1500,
        speed: 1, sfx: [{ at: k.t('approval-asked'), kind: 'approval' }],
        crops: [{ at: k.t('approval-asked') - 300, box: zoom(k.box('save-card'), 40), ease: 0.01 }],
        rings: portrait ? [] : [{ from: k.t('save-card'), to: k.t('save-answer') + 800, box: k.box('save-card') }],
        captions: [
          { at: k.t('approval-asked') - 300, text: 'Saving the report changes a file, so Jig *stops and asks*, showing exactly what it will write.' },
          { at: k.t('save-answer') - 600, text: 'Yes.' },
        ] },
      { type: 'screen', chapter: C4, header: header('The *answer*'), from: k.t('save-answer') + 1500, to: replyEnd,
        speed: fit(k.t('save-answer') + 1500, replyEnd, portrait ? 5 : 6), sfx: [{ at: k.t('research-done'), kind: 'success' }],
        captions: [{ at: k.t('save-answer') + 1500, text: 'The report is saved, and the answer arrives with its sources.' }] },
      { type: 'screen', chapter: C4, header: header('With its *sources*'), from: k.t('read-start'), to: k.t('read-end'),
        speed: fit(k.t('read-start'), k.t('read-end'), portrait ? 9 : 10),
        captions: [{ at: k.t('read-start'), text: 'Price, who can use it, discount and restrictions, each with the page it came from.' }] },
    ];
    if (!portrait) {
      const f = k.cmd('result', SHOW_FILE);
      shots.push({ type: 'term', session: 'result', title: 'Windows PowerShell · Jig workspace', chapter: C4, from: k.t('file-start'), to: f.end + 3500,
        captions: [{ at: k.t('file-start'), text: 'And the report is a real file in Jig’s workspace.' }] });
    }
    shots.push({ type: 'card', dur: portrait ? 3.2 : 3.8, card: { title: '*Jig*', sub: 'Your local, always-on agent. Open source.',
      lines: portrait ? [] : ['Runs on your own computer, with a model you choose'], avatar: { state: 'dance' } } });
    return { shots };
  },
};
