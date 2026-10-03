/* Helpers shared by the scenarios: following a real chat run, avatar checks and output parsing. */
import { ui } from '../lib/ui-map.mjs';
import { now, waitUntil } from '../lib/util.mjs';

export function parseJsonBlock(text) {
  const a = text.indexOf('{');
  const b = text.lastIndexOf('}');
  if (a < 0 || b < a) throw new Error(`No JSON object in output:\n${text.slice(-1500)}`);
  return JSON.parse(text.slice(a, b + 1));
}

/**
 * Each tool call of a run (GET /runs/<id>): the arguments the model gave, from the run's messages, and the
 * outcome Jig recorded for it in the run's step records (ok, result, error, error_type, policy). The tool
 * message's text is not parsed: it is written for the model, and Jig ends it with a "[Jig budget]" line.
 */
export function toolOutcomes(run) {
  const steps = new Map();
  /* a call that waited through a pause runs again on resume; its last record is the one that counts */
  for (const st of run.step_records || []) if (st.type === 'tool_call' && st.input?.id) steps.set(st.input.id, st);
  /* a chat run's messages replay the conversation so far: calls before its own user message belong to earlier
   * runs, unless this run carried them out (a call left waiting when a turn stopped) */
  const messages = run.messages || [];
  const turn = messages.map((m) => m.role).lastIndexOf('user');
  const out = [];
  for (const [i, msg] of messages.entries()) {
    for (const c of msg.role === 'assistant' ? msg.tool_calls || [] : []) {
      if (i < turn && !steps.has(c.id)) continue;
      const raw = c.function?.arguments;
      let args = {};
      try { args = typeof raw === 'string' ? JSON.parse(raw || '{}') : (raw || {}); } catch { args = { _raw: raw }; }
      const st = steps.get(c.id);
      const o = st?.output;
      out.push({
        id: c.id, tool: c.function?.name, args, status: st?.status ?? 'not run', ok: Boolean(o?.ok),
        result: o?.result ?? null, error: o?.error ?? st?.error ?? null, errorType: o?.error_type ?? null, policy: o?.policy ?? {},
      });
    }
  }
  return out;
}

/** An outcome in words: "write_file shopping.md: denied by the user (ApprovalDenied)". */
export function outcomeWords(o) {
  const target = o.args.path || o.args.url || o.args.command || '';
  const how = o.ok ? 'succeeded'
    : o.policy?.mode === 'refused' ? `refused in ${o.errorType === 'ModeViolation' ? 'read-only' : 'this'} mode (${o.errorType})`
      : o.policy?.approval && o.policy.approval.status !== 'approved' ? `${o.policy.approval.status} by the user (${o.errorType})`
        : `failed (${o.errorType || o.status})`;
  return `${o.tool}${target ? ` ${String(target).slice(0, 60)}` : ''}: ${how}`;
}

/** Did any call of these tools (and matching `pred`) succeed? With the outcomes of all of them, in words. */
export function happened(outcomes, tools, pred = () => true) {
  const mine = outcomes.filter((o) => tools.includes(o.tool) && pred(o));
  return { happened: mine.some((o) => o.ok), outcome: mine.length ? mine.map(outcomeWords).join('; ') : `no ${tools.join('/')} call` };
}

/**
 * Record every state change of the page's real <jig-avatar>, so the run can check that the UI showed
 * what the server's /events stream said.
 */
export async function startAvatarMonitor(page) {
  await ui.avatar(page).evaluate((el) => {
    if (window.__avLog) return;
    window.__avLog = [];
    const record = () => window.__avLog.push({ t: Date.now(), state: el.state, task: el.task, background: el.background });
    record();
    /* every state the UI really applies, however briefly; read back from the element's own getters */
    const setState = el.setState;
    el.setState = function (...args) {
      const result = setState.apply(this, args);
      record();
      return result;
    };
  });
}

export const pageAvatarLog = (page) => page.evaluate(() => window.__avLog || []);

/**
 * Check that each server avatar state that lasted at least minMs between `since` and `until` also
 * appeared on the page's avatar.
 */
export async function checkAvatarMirrors(ctx, page, { since, until = now(), minMs = 250, label }) {
  const server = ctx.jig.events.avatarStates(since).filter((s) => s.t <= until);
  const shown = await pageAvatarLog(page);
  const key = (s) => `${s.state}${s.state === 'working' ? `:${s.variant ?? s.task}` : ''}${s.background ? ' (background)' : ''}`;
  const missing = [];
  for (let i = 0; i < server.length; i++) {
    const s = server[i];
    const end = server[i + 1]?.t ?? until;
    if (end - s.t < minMs) continue;
    const seen = shown.some((p) => p.state === s.state && (s.state !== 'working' || p.task === s.variant)
      && Boolean(p.background) === Boolean(s.background) && p.t >= s.t - 100 && p.t <= end + 800);
    if (!seen) missing.push(key(s));
  }
  ctx.checks.ok(`${label}: the page avatar showed every state the server sent (${server.map(key).join(' → ')})`,
    missing.length === 0, {
      missing,
      server: server.map((x) => `${x.t - since} ${key(x)}`),
      page: shown.filter((x) => x.t >= since - 500).map((x) => `${x.t - since} ${key({ ...x, variant: x.task })}`),
    });
  return server;
}

/**
 * Answer a pending approval card in the conversation through the real UI: wait for the card, let it be read,
 * optionally open "Why am I asking?" and add a note, then press Yes or No. Marks `${label}-card`,
 * `${label}-why` (if opened) and `${label}-answer` with boxes for the edit.
 */
export async function answerCard(ctx, s, tool, { label, approve = true, why = false, note = '', readMs = 2600 } = {}) {
  const p = s.page;
  const card = ui.approvalCard(p, tool);
  await card.waitFor({ state: 'visible', timeout: 30000 });
  await ui.approve(card, tool).waitFor({ state: 'visible' });
  await card.scrollIntoViewIfNeeded();
  await ctx.sleep(400);
  ctx.mark(`${label}-card`, { box: await s.box(card) });
  await s.moveTo(ui.approvalWill(card), { ms: 900 });
  await ctx.sleep(readMs);
  if (why) {
    await s.click(ui.approvalWhySummary(card));
    await ctx.sleep(500);
    await ui.approvalVerdict(card).scrollIntoViewIfNeeded();
    ctx.mark(`${label}-why`, { box: await s.box(ui.approvalWhy(card)) });
    await s.moveTo(ui.approvalVerdict(card), { ms: 900 });
    await ctx.sleep(readMs + 1200);
  }
  if (note) {
    /* the note field sits inside "Why am I asking?" */
    if (!(await ui.approvalNote(card).isVisible())) {
      await s.click(ui.approvalWhySummary(card));
      await ui.approvalNote(card).waitFor({ state: 'visible' });
    }
    await s.type(ui.approvalNote(card), note, { charMs: 34 });
    await ctx.sleep(500);
  }
  const button = approve ? ui.approve(card, tool) : ui.deny(card, tool);
  await button.scrollIntoViewIfNeeded();
  ctx.mark(`${label}-answer`, { box: await s.box(button) });
  await s.click(button);
  const outcome = ui.chatLog(p).locator(`[data-testid="approval"][data-tool="${tool}"][data-status="${approve ? 'approved' : 'denied'}"]`).last();
  await outcome.waitFor({ state: 'visible', timeout: 15000 });
  ctx.checks.ok(`${label}: the card now says what you answered`, await ui.approvalOutcome(outcome).isVisible());
  return outcome;
}

/**
 * After a chat message has been sent in the UI: follow the real run through /events, mark the moments
 * the edit needs, and check the reply is in the UI.
 */
export async function followChat(ctx, s, { label, send, expectStates = ['thinking', 'talking'], timeoutMs = 300000, onApproval = null } = {}) {
  const { jig, checks } = ctx;
  const p = s.page;
  await startAvatarMonitor(p);
  const sent = ctx.mark(`${label}-sent`).t;
  await send();
  const start = await jig.events.waitFor('the chat run to start', (e) => e.type === 'run.start' && e.data.kind === 'chat', { since: sent - 2000 });
  const runId = start.data.run_id;
  const seen = new Set();
  const answered = new Set();
  const approvals = [];
  const end = await waitUntil(`chat run ${runId} to finish`, async () => {
    const asked = jig.events.events.find((e) => e.type === 'approval.requested' && e.data.run_id === runId && !answered.has(e.data.approval_id ?? e.data.id));
    if (asked) {
      answered.add(asked.data.approval_id ?? asked.data.id);
      if (!onApproval) throw new Error(`${label}: the run asked for approval of ${asked.data.tool}, which this scenario does not expect`);
      approvals.push({ t: asked.t, ...asked.data });
      await onApproval(asked);
    }
    for (const st of jig.events.avatarStates(sent - 2000)) {
      if (st.run_id === runId && !seen.has(st.state)) {
        seen.add(st.state);
        ctx.mark(`${label}-${st.state}`, { at: st.t });
      }
    }
    return jig.events.events.find((e) => e.type === 'run.end' && e.data.run_id === runId);
  }, { timeoutMs, intervalMs: 150 });
  ctx.mark(`${label}-done`, { status: end.data.status });
  checks.equal(`${label}: chat run finished`, end.data.status, 'done');
  for (const st of expectStates) checks.ok(`${label}: avatar went through "${st}"`, seen.has(st), [...seen]);
  const reply = ui.jigReplies(p).last();
  await waitUntil(`${label}: the reply to appear in the UI`, async () => (await reply.innerText()).trim().length > 10, { timeoutMs: 20000 });
  await waitUntil(`${label}: the Send button to be enabled again`, () => ui.chatSend(p).isEnabled(), { timeoutMs: 20000 });
  const text = (await reply.innerText()).trim();
  const run = await jig.get(`/runs/${runId}`);
  await checkAvatarMirrors(ctx, p, { since: sent, label });
  ctx.note(`${label}: run ${runId} took ${Math.round((end.t - sent) / 1000)} s, ${run.steps} steps`);
  return { runId, reply: text, final: run.final || '', steps: run.steps, states: [...seen], approvals, ms: end.t - sent };
}
