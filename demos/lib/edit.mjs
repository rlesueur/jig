/*
 * Helpers for writing a scenario's edit: shots are cut from the real capture at real marks.
 * Source times are wall-clock milliseconds from the capture. Nothing here can invent footage: a shot can
 * only show captured frames or bytes, at 1x or with a visible speed-up, and gaps between consecutive
 * shots of the same source are labelled as skipped by the renderer.
 */

export function marksOf(m) {
  const byId = new Map();
  for (const mk of m.marks) if (!byId.has(mk.id)) byId.set(mk.id, mk);
  const get = (id) => {
    const mk = byId.get(id);
    if (!mk) throw new Error(`The capture has no mark "${id}" (marks: ${[...byId.keys()].join(', ')})`);
    return mk;
  };
  return {
    has: (id) => byId.has(id),
    /** the time of a mark; marks recorded from events carry the event time in `at` */
    t: (id) => get(id).at ?? get(id).t,
    get,
    box: (id) => {
      const b = get(id).box;
      if (!b) throw new Error(`Mark "${id}" has no box`);
      return b;
    },
    /** a typed command in a console session: { start, enter, end } */
    cmd(session, text) {
      const s = m.terms.find((x) => x.id === session);
      if (!s) throw new Error(`No console session "${session}"`);
      const c = s.commands.find((x) => x.cmd === text);
      if (!c) throw new Error(`Session "${session}" has no command "${text}"`);
      return c;
    },
    term(session) {
      const s = m.terms.find((x) => x.id === session);
      if (!s) throw new Error(`No console session "${session}"`);
      return s;
    },
    dialogs: () => m.screen?.dialogs || [],
    events: m.events,
  };
}

/** The smallest whole speed-up (1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32) that fits src ms into maxOutS seconds. */
/** End a console shot at t, but never after the next command starts (shots of one source only move forwards). */
export const until = (t, next) => Math.min(t, next.start - 300);

export function fit(fromMs, toMs, maxOutS) {
  const need = (toMs - fromMs) / 1000 / maxOutS;
  for (const s of [1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32]) if (s >= need) return s;
  throw new Error(`Cannot fit ${Math.round((toMs - fromMs) / 1000)} s into ${maxOutS} s even at x32`);
}

/** A crop box of the given output aspect ratio centred on a box (frame pixels), with some padding. */
export function cropAround(box, aspect, { pad = 60, minW = 640 } = {}) {
  let w = Math.max(box.w + 2 * pad, minW);
  let h = w / aspect;
  if (h < box.h + 2 * pad) {
    h = box.h + 2 * pad;
    w = h * aspect;
  }
  w = Math.min(w, 1920);
  h = Math.min(h, 1080);
  const x = Math.min(Math.max(box.x + box.w / 2 - w / 2, 0), 1920 - w);
  const y = Math.min(Math.max(box.y + box.h / 2 - h / 2, 0), 1080 - h);
  return { x, y, w, h };
}

export const FULL = { x: 0, y: 0, w: 1920, h: 1080 };
