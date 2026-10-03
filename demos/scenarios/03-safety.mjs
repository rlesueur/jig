/*
 * Launch demo: Safety. Ordinary requests show how Jig keeps you in charge: saving a file asks first, the card
 * explains why ("Why am I asking?"), a No with a note is respected and nothing is written; with "Just look,
 * don't touch" turned on, Jig cannot change anything even when asked; and History records every answer.
 *
 * Capability checks: the save asked first, the denial was recorded with its note and no file was written,
 * the read-only conversation ran in read-only mode and wrote nothing without asking, and the audit log
 * has the denial but not the note's words (it never holds what was said; History does, and can be deleted).
 */
import { existsSync } from 'node:fs';
import path from 'node:path';
import { cropAround, fit, FULL, marksOf } from '../lib/edit.mjs';
import { ui } from '../lib/ui-map.mjs';
import { answerCard, followChat } from './common.mjs';
import { backToChat, openSettings, signIn, union } from './common-ui.mjs';

const FILE = 'shopping.md';
const ASK = `Please save a shopping list to ${FILE}: oat milk, lentils, spinach and lemons.`;
const NOTE = 'Not yet, please. I want to add a few more things first.';
const ASK_RO = `Now save the list to ${FILE}, and add rice to it.`;

export default {
  title: 'Safety',
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

    /* Settings: saving files asks first */
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
    await backToChat(s);
    await ctx.sleep(600);

    /* 1. a save asks first; "Why am I asking?"; No, with a note */
    ctx.mark('ask-start');
    await s.type(ui.chatInput(p), ASK, { charMs: 24 });
    ctx.mark('ask-typed', { box: await s.box(ui.chatInput(p)) });
    let asked = null;
    const deny = await followChat(ctx, s, {
      label: 'save', send: () => s.press('Enter'),
      onApproval: async (a) => {
        checks.equal('the approval asked for is the file save', a.data.tool, 'write_file');
        asked = a;
        ctx.mark('approval-asked', { at: a.t });
        await answerCard(ctx, s, 'write_file', { label: 'card', approve: false, why: true, note: NOTE, readMs: 2800 });
      },
    });
    checks.ok('the save asked first', Boolean(asked));
    checks.ok(`${FILE} was not written after No`, !existsSync(path.join(jig.workspace, FILE)));
    const approval = (await jig.get('/approvals?status=denied')).find((a) => a.run_id === deny.runId);
    checks.equal('the denial kept its note', approval?.note, NOTE);
    ctx.mark('deny-reply', { box: await s.box(ui.jigReplies(p).last()) });
    await ctx.sleep(5000);

    /* 2. "Just look, don't touch" */
    await openSettings(s, 'chat');
    const ro = ui.readOnlySwitch(p);
    ctx.mark('ro-switch', { box: await s.box(ro.locator('xpath=ancestor::*[self::label or self::div][1]')) });
    await s.click(ro);
    checks.ok('"Just look, don\'t touch" is on', await ro.isChecked());
    await ctx.sleep(1500);
    await backToChat(s);
    await ui.readOnlyNote(p).waitFor({ state: 'visible' });
    ctx.mark('ro-note', { box: await s.box(ui.readOnlyNote(p)) });
    await ctx.sleep(1800);
    ctx.mark('ro-start');
    await s.type(ui.chatInput(p), ASK_RO, { charMs: 26 });
    const ro2 = await followChat(ctx, s, { label: 'ro', send: () => s.press('Enter') });
    const run = await jig.get(`/runs/${ro2.runId}`);
    checks.equal('the conversation ran read-only', run.mode, 'research');
    checks.ok(`${FILE} was not written in read-only mode`, !existsSync(path.join(jig.workspace, FILE)));
    const refused = (await jig.get(`/audit?run_id=${ro2.runId}&kind=policy.mode_violation`)).length;
    ctx.note(`read-only: ${refused ? `${refused} write attempt(s) refused by the core rule` : 'the model was not offered write tools and did not try'}; reply: ${ro2.final.slice(0, 300)}`);
    ctx.mark('ro-reply', { box: await s.box(ui.jigReplies(p).last()), refused });
    await ctx.sleep(6500);

    /* 3. History: the answer, and the audit log */
    await openSettings(s, 'history');
    const done = ui.answeredList(p).locator(`[data-testid="approval"][data-tool="write_file"]`).first();
    await done.waitFor({ state: 'visible' });
    await done.scrollIntoViewIfNeeded();
    ctx.mark('history', { box: await s.box(done) });
    await s.moveTo(done, { ms: 900 });
    await ctx.sleep(3200);
    await s.type(ui.auditKind(p), 'approval', { charMs: 60 });
    await s.click(ui.auditFilter(p));
    const row = ui.auditRow(p, 'approval.resolved').first();
    await row.waitFor({ state: 'visible' });
    await row.scrollIntoViewIfNeeded();
    await s.click(row);
    const detail = ui.auditList(p).locator('details[open]').first();
    await detail.waitFor({ state: 'visible' });
    ctx.mark('audit', { box: await s.box(detail) });
    const entry = await detail.innerText();
    checks.ok('the audit entry has the denial', entry.includes('write_file denied'));
    checks.ok('the audit entry does not hold the note', !entry.includes(NOTE));
    await s.moveTo(detail, { ms: 900 });
    await ctx.sleep(4200);
    ctx.mark('end');
    await s.stopRecording();
  },

  edit(m, format) {
    const k = marksOf(m);
    const portrait = format === 'portrait';
    const aspect = portrait ? 1032 / 774 : 16 / 9;
    const zoom = (box, pad = 80) => (portrait ? cropAround(box, aspect, { pad }) : FULL);
    const header = (title) => (portrait ? { kicker: 'Jig · safety', title } : null);
    const C1 = 'It asks first';
    const C2 = 'Just look, don’t touch';
    const C3 = 'A record of everything';
    const close = (box) => cropAround(box, aspect, { pad: portrait ? 40 : 150 });
    const saveEnd = k.t('save-done') + 5000;
    const roEnd = k.t('ro-done') + 6500;
    return {
      shots: [
        { type: 'card', dur: portrait ? 2.8 : 3.4, card: { kicker: 'Jig · real run', title: 'You stay *in charge*',
          sub: 'Jig asks before it changes anything, explains why, and keeps a record.', avatar: { state: 'approval' } } },
        { type: 'screen', chapter: 'Your rules', header: header('Your *rules*'), from: k.t('rules') - 600, to: k.t('rules-saved') + 1500,
          speed: fit(k.t('rules') - 600, k.t('rules-saved') + 1500, 4), crops: [{ at: k.t('rules') - 600, box: zoom(k.box('rules-saved'), 160), ease: 0.01 }],
          rings: [{ from: k.t('rules-saved') - 200, to: k.t('rules-saved') + 1500, box: k.box('rules-saved') }],
          captions: [{ at: k.t('rules') - 600, text: 'One setting: saving files should *ask me first*.' }] },
        { type: 'screen', chapter: C1, header: header('It *asks* first'), from: k.t('ask-start'), to: k.t('approval-asked') - 200,
          speed: fit(k.t('ask-start'), k.t('approval-asked') - 200, 6),
          crops: [{ at: k.t('ask-start'), box: zoom(k.box('ask-typed'), 120), ease: 0.01 }],
          captions: [{ at: k.t('ask-start'), text: 'An everyday request: save a shopping list.' }] },
        { type: 'screen', chapter: C1, header: header('It *asks* first'), from: k.t('approval-asked') - 200, to: k.t('card-why') - 200,
          speed: 1, sfx: [{ at: k.t('approval-asked'), kind: 'approval' }],
          crops: [{ at: k.t('approval-asked') - 200, box: zoom(k.box('card-card'), 40), ease: 0.01 }],
          rings: portrait ? [] : [{ from: k.t('card-card'), to: k.t('card-why') - 200, box: k.box('card-card') }],
          captions: [{ at: k.t('approval-asked') - 200, text: 'Saving changes a file, so Jig *stops and asks*, showing exactly what it will write.' }] },
        { type: 'screen', chapter: C1, header: header('Why am I *asking*?'), from: k.t('card-why') - 200, to: k.t('card-answer') + 1200,
          speed: 1,
          crops: [{ at: k.t('card-why') - 200, box: zoom(k.box('card-why'), 40), ease: 0.6 }],
          rings: portrait ? [] : [{ from: k.t('card-why'), to: k.t('card-answer') - 400, box: k.box('card-why') }],
          captions: [
            { at: k.t('card-why') - 200, text: '*Why am I asking?* shows the rule that applies, and what Jig’s safety check made of it.' },
            { at: k.t('card-answer') - 3500, text: 'Not today. Say *No*, with a note for Jig.' },
          ] },
        { type: 'screen', chapter: C1, header: header('No means *no*'), from: k.t('card-answer') + 1200, to: k.t('save-done'),
          speed: fit(k.t('card-answer') + 1200, k.t('save-done'), 3),
          captions: [{ at: k.t('card-answer') + 1200, text: 'Jig takes the answer. Nothing is saved.' }] },
        { type: 'screen', chapter: C1, header: header('No means *no*'), from: k.t('save-done'), to: saveEnd, speed: 1,
          crops: [{ at: k.t('save-done'), box: close(k.box('deny-reply')), ease: 0.8 }],
          captions: [{ at: k.t('save-done'), text: 'Jig takes the answer. Nothing is saved.' }] },
        { type: 'screen', chapter: C2, header: header('Just *look*'), from: k.t('ro-switch') - 600, to: k.t('ro-start'),
          speed: fit(k.t('ro-switch') - 600, k.t('ro-start'), 5),
          crops: [{ at: k.t('ro-switch') - 600, box: zoom(k.box('ro-switch'), 120), ease: 0.01 }, { at: k.t('ro-note') - 200, box: zoom(k.box('ro-note'), 120), ease: 0.6 }],
          rings: [{ from: k.t('ro-switch'), to: k.t('ro-note') - 300, box: k.box('ro-switch') }, { from: k.t('ro-note'), to: k.t('ro-start'), box: k.box('ro-note') }],
          captions: [{ at: k.t('ro-switch') - 600, text: 'Just want answers? Turn on *Just look, don’t touch*.' }] },
        { type: 'screen', chapter: C2, header: header('Just *look*'), from: k.t('ro-start'), to: k.t('ro-done'), speed: fit(k.t('ro-start'), k.t('ro-done'), 5),
          captions: [{ at: k.t('ro-start'), text: 'Now ask it to save the list…' }] },
        { type: 'screen', chapter: C2, header: header('Just *look*'), from: k.t('ro-done'), to: roEnd, speed: 1,
          crops: [{ at: k.t('ro-done'), box: close(k.box('ro-reply')), ease: 0.8 }],
          captions: [{ at: k.t('ro-done'), text: '…and it can’t. In this mode Jig can read and look things up, but it won’t touch your files or send anything.' }] },
        { type: 'screen', chapter: C3, header: header('A *record*'), from: k.t('history') - 900, to: k.t('end'),
          speed: fit(k.t('history') - 900, k.t('end'), 8),
          crops: [{ at: k.t('history') - 900, box: zoom(k.box('history'), 60), ease: 0.01 }, { at: k.t('audit') - 300, box: zoom(k.box('audit'), 60), ease: 0.7 }],
          rings: [{ from: k.t('history'), to: k.t('audit') - 1500, box: k.box('history') }, { from: k.t('audit'), to: k.t('end'), box: k.box('audit') }],
          captions: [
            { at: k.t('history') - 900, text: 'Every answer you give is kept in *History*, with your note…' },
            { at: k.t('audit') - 300, text: '…and in the audit log, alongside everything Jig did.' },
          ] },
        { type: 'card', dur: portrait ? 3.2 : 3.8, card: { title: '*Jig*', sub: 'Your local, always-on agent. Open source.',
          lines: portrait ? [] : ['Asks before it acts, and shows its working'], avatar: { state: 'dance' } } },
      ],
    };
  },
};
