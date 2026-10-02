/*
 * Demo 4: Memory. Tell Jig something in chat; the real model stores it with its memory tool; it shows
 * in the Memory panel; edit it there; a new conversation recalls the edited version; forget it (the
 * real confirm dialog); search shows it is gone, the API returns 404, and a new conversation no
 * longer knows it. The audit log records each change.
 */
import { cropAround, fit, FULL, marksOf } from '../lib/edit.mjs';
import { BASE } from '../lib/jig.mjs';
import { ui } from '../lib/ui-map.mjs';
import { waitUntil } from '../lib/util.mjs';
import { followChat } from './common.mjs';
import { openTab, signIn, union } from './common-ui.mjs';

const TELL = 'Please remember this for later: my sister Ana’s birthday is on 14 March, and her favourite flowers are yellow tulips.';
const EDITED = 'Ana (my sister) has her birthday on 14 March. Her favourite flowers are sunflowers.';
const ASK = 'What should I get my sister Ana for her birthday, and when is it?';

export default {
  title: 'Memory',

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
    const mem = added.find((x) => /tulip/i.test(x.content) && /14 March|March 14/i.test(x.content));
    checks.ok('the model stored the fact with its memory tool', Boolean(mem), added);
    checks.equal('the memory came from the agent, not the user', mem.source === 'user' ? 'user' : 'agent', 'agent');
    ctx.note(`stored memory #${mem.id}: ${mem.content} (source ${mem.source})`);
    ctx.mark('tell-reply', { box: await s.box(ui.jigReplies(p).last()) });
    await ctx.sleep(2500);

    /* 2. see it in the Memory panel */
    await openTab(s, 'Memory');
    const row = ui.panel(p, 'Memory').getByText(mem.content, { exact: true });
    /* FINDINGS F3: the panel is not refreshed when the agent adds a memory, so the demo always presses Show all */
    await ctx.sleep(1200);
    const stale = (await row.count()) === 0;
    ctx.note(stale ? 'F3 reproduced: the Memory panel did not show the agent\'s new memory until Show all' : 'F3 not seen: the panel already showed the new memory');
    ctx.mark('mem-tab', { stale });
    await s.click(ui.memoryShowAll(p));
    await row.waitFor({ state: 'visible' });
    ctx.mark('mem-shown', { box: union(await s.box(row), await s.box(ui.memoryForget(p, mem.id))) });
    await s.moveTo(row, { ms: 900 });
    await ctx.sleep(2200);

    /* 3. edit it */
    await s.click(ui.memoryEdit(p, mem.id));
    const field = ui.memoryEditContent(p, mem.id);
    await field.waitFor({ state: 'visible' });
    ctx.mark('edit-open', { box: await s.box(field) });
    await s.type(field, EDITED, { charMs: 30, replace: true });
    await ctx.sleep(600);
    await s.click(ui.memorySave(p));
    const edited = ui.panel(p, 'Memory').getByText(EDITED, { exact: true });
    await edited.waitFor({ state: 'visible' });
    ctx.mark('edit-saved', { box: union(await s.box(edited), await s.box(ui.memoryForget(p, mem.id))) });
    checks.equal('PATCH stored the edited text', (await jig.get(`/memory/${mem.id}`)).content, EDITED);
    await ctx.sleep(2200);

    /* 4. a new conversation recalls the edited memory */
    await openTab(s, 'Chat');
    await s.click(ui.chatNew(p));
    ctx.mark('recall-start');
    await s.type(ui.chatInput(p), ASK, { charMs: 24 });
    const recall = await followChat(ctx, s, { label: 'recall', send: () => s.press('Enter') });
    checks.ok('a new conversation recalls the edited memory (sunflowers, 14 March)',
      /sunflower/i.test(recall.reply) && /14|March/i.test(recall.reply), recall.reply);
    ctx.mark('recall-reply', { box: await s.box(ui.jigReplies(p).last()) });
    await ctx.sleep(3000);

    /* 5. forget it */
    await openTab(s, 'Memory');
    await ui.panel(p, 'Memory').getByText(EDITED, { exact: true }).waitFor({ state: 'visible' });
    ctx.mark('forget-start', { box: await s.box(ui.memoryForget(p, mem.id)) });
    await s.click(ui.memoryForget(p, mem.id));
    await waitUntil('the memory to leave the list', async () => (await ui.panel(p, 'Memory').getByText(EDITED, { exact: true }).count()) === 0, { timeoutMs: 15000 });
    const dialog = s.dialogs.at(-1);
    checks.ok('Forget asked for confirmation first', dialog?.type === 'confirm' && /Forget this memory/.test(dialog.message), dialog);
    ctx.mark('forgotten');
    const gone = await fetch(`${BASE}/memory/${mem.id}`, { headers: { Authorization: `Bearer ${jig.token}` } });
    checks.equal('GET /memory/<id> returns 404 after forgetting', gone.status, 404);
    await s.paste(ui.memorySearch(p), 'Ana sunflowers');
    await s.click(ui.memorySearchButton(p));
    await ctx.sleep(1200);
    checks.equal('search finds nothing about Ana', (await jig.get('/memory?q=Ana')).filter((x) => /Ana/.test(x.content)).length, 0);
    await ui.panel(p, 'Memory').getByText(/Nothing in Jig.s memory matches/).waitFor({ state: 'visible' });
    checks.equal('the panel lists no memories for the search', await ui.panel(p, 'Memory').getByRole('button', { name: /^Forget memory / }).count(), 0);
    ctx.mark('search-empty', { box: union(await s.box(ui.memorySearch(p)), await s.box(ui.memoryHeading(p))) });
    await ctx.sleep(2500);

    /* 6. and Jig no longer knows */
    await openTab(s, 'Chat');
    await s.click(ui.chatNew(p));
    ctx.mark('after-start');
    await s.type(ui.chatInput(p), ASK, { charMs: 24 });
    const after = await followChat(ctx, s, { label: 'after', send: () => s.press('Enter') });
    checks.ok('after forgetting, Jig no longer knows the birthday or the flowers', !/sunflower|tulip|14 March|March 14/i.test(after.reply), after.reply);
    ctx.mark('after-reply', { box: await s.box(ui.jigReplies(p).last()) });
    await ctx.sleep(3200);
    await s.stopRecording();

    const kinds = (await jig.get('/audit?kind=memory')).map((r) => `${r.kind}:${r.data.memory_id ?? ''}`);
    checks.ok('the audit log has the edit and the forget', kinds.includes(`memory.edited:${mem.id}`) && kinds.includes(`memory.forgotten:${mem.id}`), kinds);
    ctx.note(`replies: tell="${tell.reply.slice(0, 120)}" | recall="${recall.reply.slice(0, 160)}" | after="${after.reply.slice(0, 160)}"`);
  },

  edit(m) {
    const k = marksOf(m);
    const C1 = '1 · Tell it';
    const C2 = '2 · See and edit it';
    const C3 = '3 · It remembers';
    const C4 = '4 · Forget it';
    const dlg = k.dialogs().find((d) => d.type === 'confirm');
    const chat = (label, from, to, max) => ({ from, to, speed: fit(from, to, max) });
    const tell = chat('tell', k.t('tell-start'), k.t('tell-done') + 2500, 14);
    const recall = chat('recall', k.t('recall-start'), k.t('recall-done') + 3000, 14);
    const after = chat('after', k.t('after-start'), k.t('after-done') + 3200, 14);
    return {
      shots: [
        { type: 'card', dur: 3.4, card: { kicker: 'Jig · demo', title: 'Memory', sub: 'It remembers what you tell it. You can see, edit and delete every word.', avatar: { state: 'idle' } } },
        { type: 'screen', chapter: C1, ...tell,
          captions: [
            { at: tell.from, dur: 4.5, text: 'Tell Jig something worth remembering.' },
            { at: k.t('tell-thinking'), text: 'The model decides to store it, using its *memory* tool.' },
          ] },
        { type: 'screen', chapter: C2, from: k.t('mem-tab') - 1200, to: k.t('edit-open'),
          crops: [{ at: k.t('mem-tab') - 1200, box: cropAround(k.box('mem-shown'), 16 / 9, { pad: 220 }), ease: 0.01 }],
          rings: [{ from: k.t('mem-shown'), to: k.t('edit-open'), box: k.box('mem-shown') }],
          captions: [{ at: k.t('mem-tab') - 1200, text: 'Every memory is listed in plain words, with where it came from.' }] },
        { type: 'screen', chapter: C2, from: k.t('edit-open'), to: k.t('edit-saved') + 2000, speed: fit(k.t('edit-open'), k.t('edit-saved') + 2000, 4),
          crops: [{ at: k.t('edit-open'), box: cropAround(k.box('edit-open'), 16 / 9, { pad: 220 }), ease: 0.01 }, { at: k.t('edit-saved') + 1500, box: FULL, ease: 0.6 }],
          captions: [{ at: k.t('edit-open'), text: 'It is yours to correct. *Edit* it, and Save.' }] },
        { type: 'screen', chapter: C3, ...recall,
          captions: [
            { at: recall.from, dur: 4, text: 'A brand-new conversation, with no chat history…' },
            { at: k.t('recall-talking'), text: '…and Jig answers from the *edited* memory.' },
          ] },
        { type: 'screen', chapter: C4, from: k.t('forget-start') - 800, to: k.t('search-empty') + 2400, speed: fit(k.t('forget-start') - 800, k.t('search-empty') + 2400, 3),
          rings: [{ from: k.t('forget-start') - 600, to: k.t('forgotten'), box: k.box('forget-start') }, { from: k.t('search-empty') - 300, to: k.t('search-empty') + 2400, box: k.box('search-empty') }],
          callouts: dlg ? [{ from: dlg.t, to: dlg.answeredAt + 600, label: 'The UI asks first', text: dlg.message }] : [],
          captions: [
            { at: k.t('forget-start') - 800, dur: 4, text: '*Forget* deletes it from the database and the search index.' },
            { at: k.t('search-empty') - 300, text: 'Search for it: nothing left.' },
          ] },
        { type: 'screen', chapter: C4, ...after,
          captions: [
            { at: after.from, dur: 4, text: 'Ask again, in a new conversation…' },
            { at: k.t('after-talking'), text: '…and Jig no longer knows. Every change is in the audit log.' },
          ] },
        { type: 'card', dur: 3.8, card: { title: '*Jig*', sub: 'Memory you can read, edit and delete. Open source, Apache-2.0.', avatar: { state: 'idle' } } },
      ],
    };
  },
};
