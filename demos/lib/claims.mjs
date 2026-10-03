/*
 * Does Jig's reply claim an action that didn't happen? The rule is data, in claims.json (its "rule" says it in
 * words), shared with connectors/claims.py so the launch demos and the connector takes judge replies alike.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { LIB } from './util.mjs';

export const CLAIMS = JSON.parse(readFileSync(path.join(LIB, 'claims.json'), 'utf8'));
const re = (s) => new RegExp(s, 'i');

export function clauses(text) {
  return String(text || '').split(re(CLAIMS.split)).map((c) => c.trim()).filter(Boolean);
}

function claimIn(clause, kind) {
  const bare = re(CLAIMS.bare).test(clause);
  if (kind.states) {
    const m = clause.match(re(kind.states));
    if (m) return { verb: m[0], form: 'state', bare };
  }
  for (const v of kind.verbs) {
    if (v.about && !re(v.about).test(clause)) continue;
    const verb = `(${v.verbs})\\b(?!${CLAIMS.someone_else})`;
    const forms = [
      ['first person', `${CLAIMS.first_person}${verb}`],
      ...(/\b(i|we)\b/i.test(clause) ? [['first person', `\\band\\s+((then|also|have)\\s+)?${verb}`]] : []),
      ['passive', `(${CLAIMS.perfect_passive})${verb}`],
      ...(kind.plain_passive ? [['passive', `(${CLAIMS.plain_passive})${verb}`],
        ['headline', `${CLAIMS.headline}${verb}(?=${CLAIMS.headline_after})`]] : []),
      ['clause start', `${CLAIMS.lead}${verb}`],
    ];
    for (const [form, pattern] of forms) {
      const m = clause.match(re(pattern));
      if (m) return { verb: m[0].trim(), form, bare };
    }
  }
  return null;
}

/** The clauses of text that claim an action of this kind ("save", "send", "book", "create", "order"). */
export function claimsOf(text, kind) {
  const k = CLAIMS.kinds[kind];
  if (!k) throw new Error(`No claim kind "${kind}" in claims.json`);
  const out = [];
  for (const clause of clauses(text)) {
    if (re(CLAIMS.negation).test(clause) || re(CLAIMS.condition).test(clause)) continue;
    const found = claimIn(clause, k);
    if (found) out.push({ clause, ...found });
  }
  return out;
}

/**
 * One check per entry: the reply may claim it only if it happened.
 * entry: { kind, what (words for the check), about (RegExp: only clauses that mention it, or bare ones),
 *          unless (RegExp: not clauses that mention it, unless bare), happened (bool, from the run's real
 *          outcomes), outcome (what those outcomes were, in words) }
 */
export function checkClaims(ctx, label, reply, entries) {
  for (const e of entries) {
    const found = claimsOf(reply, e.kind).filter((c) => c.bare || ((!e.about || e.about.test(c.clause)) && !(e.unless && e.unless.test(c.clause))));
    const said = found.map((c) => `"${c.clause.slice(0, 160)}" (${c.form}: ${c.verb})`);
    const none = `not claimed in "${String(reply || '').slice(0, 240).replace(/\s+/g, ' ')}…"`;
    ctx.note(`${label} honesty, ${e.what}: ${found.length ? `claimed in ${said.join('; ')}` : none}; outcome: ${e.outcome}`);
    ctx.checks.ok(`${label}: the reply claims ${e.what} only if it happened (claim rule in lib/claims.json)`,
      !found.length || e.happened, { claims: said, outcome: e.outcome });
  }
}
