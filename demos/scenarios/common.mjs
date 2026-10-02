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
  await checkAvatarMirrors(ctx, p, { since: sent, label });
  return { runId, reply: text, states: [...seen], approvals };
}
