/*
 * Launch demo: Memory. A preference told in chat is stored by the real model with its memory tool and shows
 * straight away in Settings > What Jig remembers about you. It is edited there, a brand-new conversation uses
 * the edited version, then it is forgotten (the real confirm dialog) and a new conversation no longer uses it.
 *
 * Capability checks: the agent stored the preference itself, the list showed it without a refresh, the edit
 * was saved, the new conversation used it, the memory is gone from the API after forgetting, the later reply
 * does not use it, and the audit log has the edit and the forget.
 */
import { cropAround, fit, FULL, marksOf } from '../lib/edit.mjs';
import { BASE } from '../lib/jig.mjs';
import { ui } from '../lib/ui-map.mjs';
import { waitUntil } from '../lib/util.mjs';
import { followChat } from './common.mjs';
import { backToChat, openSettings, signIn, union } from './common-ui.mjs';

const TELL = 'Please remember this for the future: I’m vegetarian, and on weeknights I want dinners that take under 30 minutes.';
const EDITED = 'The user is vegan: no meat, fish, eggs or dairy.';
const ASK = 'Suggest one dinner for tonight. Keep it to two or three sentences.';
const DIET = /vegan|vegetarian|plant-based|meat-free/i;

export default {
  title: 'Memory',
  formats: ['landscape'],
  theme: 'jig-light',
  scheme: 'light',

  async capture(ctx) {
    const { jig, checks } = ctx;
    await jig.start();
    const s = await ctx.screen();
    const p = s.page;
    await signIn(ctx, s);
    await s.startRecording();
    await ctx.sleep(1000);

    /* 1. tell it */
    ctx.mark('tell-start', { avatar: await s.box(ui.avatarRegion(p)) });
    await s.type(ui.chatInput(p), TELL, { charMs: 22 });
    const tell = await followChat(ctx, s, { label: 'tell', send: () => s.press('Enter') });
    const added = await jig.get('/memory');
    const mem = added.find((x) => /vegetarian/i.test(x.content));
    checks.ok('the model stored the diet with its memory tool', Boolean(mem), added);
    checks.ok('the model stored the 30-minute preference too', added.some((x) => /30/.test(x.content)), added);
    ctx.note(`memories after telling: ${added.map((x) => `#${x.id} "${x.content}"`).join(' | ')}`);
    checks.equal('the memory came from the agent, not the user', mem.source === 'user' ? 'user' : 'agent', 'agent');
    ctx.note(`stored memory #${mem.id}: ${mem.content} (source ${mem.source})`);
    ctx.mark('tell-reply', { box: await s.box(ui.jigReplies(p).last()) });
    await ctx.sleep(4500);

    /* 2. it is listed in Settings straight away */
    await openSettings(s, 'memory');
    const row = ui.memoryItems(p).filter({ hasText: mem.content });
    await row.waitFor({ state: 'visible', timeout: 5000 });
    checks.ok('Settings listed the new memory without pressing Show all', true);
    ctx.mark('mem-shown', { box: await s.box(row) });
    await s.moveTo(row, { ms: 900 });
    await ctx.sleep(2600);

    /* 3. edit it */
    await s.click(ui.memoryEdit(p, mem.id));
    const field = ui.memoryEditContent(p, mem.id);
    await field.waitFor({ state: 'visible' });
    ctx.mark('edit-open', { box: await s.box(field) });
    await s.type(field, EDITED, { charMs: 28, replace: true });
    await ctx.sleep(600);
    await s.click(ui.memorySave(p));
    const edited = ui.memoryItems(p).filter({ hasText: EDITED });
    await edited.waitFor({ state: 'visible' });
    ctx.mark('edit-saved', { box: await s.box(edited) });
    checks.equal('the edit was saved', (await jig.get(`/memory/${mem.id}`)).content, EDITED);
    await ctx.sleep(2200);

    /* 4. a new conversation uses the edited memory */
    await backToChat(s);
    await s.click(ui.chatNew(p));
    ctx.mark('use-start');
    await s.type(ui.chatInput(p), ASK, { charMs: 26 });
    const use = await followChat(ctx, s, { label: 'use', send: () => s.press('Enter') });
    checks.ok('a new conversation used the edited memory (vegan)', /vegan|plant-based/i.test(use.final), use.final);
    ctx.mark('use-reply', { box: await s.box(ui.jigReplies(p).last()) });
    await ctx.sleep(6000);

    /* 5. forget it */
    await openSettings(s, 'memory');
    await edited.waitFor({ state: 'visible' });
    ctx.mark('forget-start', { box: await s.box(edited) });
    await s.click(ui.memoryForget(p, mem.id));
    await waitUntil('the memory to leave the list', async () => (await edited.count()) === 0, { timeoutMs: 15000 });
    const dialog = s.dialogs.at(-1);
    checks.ok('Forget asked for confirmation first', dialog?.type === 'confirm' && /Forget this memory/.test(dialog.message), dialog);
    ctx.mark('forgotten', { box: union(await s.box(ui.memoryHeading(p)), await s.box(ui.memoryList(p))) });
    const gone = await fetch(`${BASE}/memory/${mem.id}`, { headers: { Authorization: `Bearer ${jig.token}` } });
    checks.equal('the memory is gone (GET returns 404)', gone.status, 404);
    checks.equal('no memory mentions the diet any more', (await jig.get('/memory')).filter((x) => DIET.test(x.content)).length, 0);
    await ctx.sleep(2400);

    /* 6. and Jig no longer uses it */
    await backToChat(s);
    await s.click(ui.chatNew(p));
    ctx.mark('after-start');
    await s.type(ui.chatInput(p), ASK, { charMs: 26 });
    const after = await followChat(ctx, s, { label: 'after', send: () => s.press('Enter') });
    checks.ok('after forgetting, the reply does not use the preference', !DIET.test(after.final), after.final);
    ctx.mark('after-reply', { box: await s.box(ui.jigReplies(p).last()) });
    await ctx.sleep(6500);
    await s.stopRecording();

    const kinds = (await jig.get('/audit?kind=memory')).map((r) => `${r.kind}:${r.data.memory_id ?? ''}`);
    checks.ok('the audit log has the edit and the forget', kinds.includes(`memory.edited:${mem.id}`) && kinds.includes(`memory.forgotten:${mem.id}`), kinds);
    ctx.note(`replies: tell="${tell.final.slice(0, 160)}" | use="${use.final.slice(0, 300)}" | after="${after.final.slice(0, 300)}"`);
  },

  edit(m) {
    const k = marksOf(m);
    const C1 = 'Tell it';
    const C2 = 'See it, change it';
    const C3 = 'It uses it';
    const C4 = 'Forget it';
    const dlg = k.dialogs().find((d) => d.type === 'confirm');
    /* each conversation (sped up if needed), then Jig's reply in close, in real time, while it is read */
    const chat = (label, chapter, holdMs, ask, answer) => {
      const from = k.t(`${label}-start`);
      const done = k.t(`${label}-done`);
      return [
        { type: 'screen', chapter, from, to: done, speed: fit(from, done, 6),
          captions: [{ at: from, text: ask }] },
        { type: 'screen', chapter, from: done, to: done + holdMs, speed: 1,
          crops: [{ at: done, box: cropAround(k.box(`${label}-reply`), 16 / 9, { pad: 150 }), ease: 0.8 }],
          captions: [{ at: done, text: answer }] },
      ];
    };
    return {
      shots: [
        { type: 'card', dur: 3.4, card: { kicker: 'Jig · real run', title: 'Memory you *control*',
          sub: 'It remembers what you tell it. You can read, change and delete every word.', avatar: { state: 'idle' } } },
        ...chat('tell', C1, 4500, 'Tell Jig a preference worth remembering.', 'Jig keeps it, in its *memory*.'),
        { type: 'screen', chapter: C2, from: k.t('tell-done') + 4500, to: k.t('edit-open'), speed: fit(k.t('tell-done') + 4500, k.t('edit-open'), 5),
          crops: [{ at: k.t('tell-done') + 4500, box: FULL, ease: 0.01 }, { at: k.t('mem-shown') - 300, box: cropAround(k.box('mem-shown'), 16 / 9, { pad: 200 }), ease: 0.7 }],
          rings: [{ from: k.t('mem-shown'), to: k.t('edit-open'), box: k.box('mem-shown') }],
          captions: [{ at: k.t('tell-done') + 4500, text: 'It is listed straight away, in plain words, under *What Jig remembers about you*.' }] },
        { type: 'screen', chapter: C2, from: k.t('edit-open'), to: k.t('edit-saved') + 2000, speed: fit(k.t('edit-open'), k.t('edit-saved') + 2000, 5),
          crops: [{ at: k.t('edit-open'), box: cropAround(k.box('edit-open'), 16 / 9, { pad: 200 }), ease: 0.01 }, { at: k.t('edit-saved') + 1500, box: FULL, ease: 0.6 }],
          captions: [{ at: k.t('edit-open'), text: 'Plans changed? *Edit* it: vegetarian becomes vegan.' }] },
        ...chat('use', C3, 6000, 'A brand-new conversation, with no chat history…', '…and Jig suggests a dinner that fits the *edited* memory.'),
        { type: 'screen', chapter: C4, from: k.t('forget-start') - 800, to: k.t('forgotten') + 2400, speed: fit(k.t('forget-start') - 800, k.t('forgotten') + 2400, 5),
          rings: [{ from: k.t('forget-start') - 600, to: k.t('forgotten'), box: k.box('forget-start') }],
          callouts: dlg ? [{ from: dlg.t, to: dlg.answeredAt + 600, label: 'Jig checks first', text: dlg.message }] : [],
          captions: [{ at: k.t('forget-start') - 800, text: '*Forget* deletes it, from the list and from the search index.' }] },
        ...chat('after', C4, 6500, 'Ask the same question again…', '…and Jig no longer assumes vegan. Every change is in the audit log.'),
        { type: 'card', dur: 3.8, card: { title: '*Jig*', sub: 'Your local, always-on agent. Open source.',
          lines: ['Memory you can read, edit and delete'], avatar: { state: 'dance' } } },
      ],
    };
  },
};
