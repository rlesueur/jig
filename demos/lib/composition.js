/*
 * Renders one output frame at a time from a plan computed by render.mjs.
 * Screen shots show the captured frame whose timestamp is the latest at or before the source time.
 * Console shots replay the exact captured bytes into xterm.js up to the source time.
 */
const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const FADE = 0.35;

let plan = null;
let avatarReady = null;
const terms = new Map();
let lastCardKey = '';
let activeTerm = null;

function richText(el, text) {
  el.replaceChildren();
  const parts = String(text).split(/(\*[^*]+\*|`[^`]+`)/);
  for (const part of parts) {
    if (/^\*[^*]+\*$/.test(part)) el.append(Object.assign(document.createElement('span'), { className: 'hl', textContent: part.slice(1, -1) }));
    else if (/^`[^`]+`$/.test(part)) el.append(Object.assign(document.createElement('code'), { className: 'code', textContent: part.slice(1, -1) }));
    else el.append(document.createTextNode(part));
  }
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function termTheme() {
  const ansi = cssVar('--term-ansi').split(/\s+/);
  const names = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'];
  const theme = { background: 'rgba(0,0,0,0)', foreground: cssVar('--term-fg'), cursor: cssVar('--term-cursor'), cursorAccent: '#000' };
  names.forEach((n, i) => {
    theme[n] = ansi[i];
    theme[`bright${n[0].toUpperCase()}${n.slice(1)}`] = ansi[i + 8];
  });
  return theme;
}

async function loadTerm(id, spec) {
  const res = await fetch(`/cap/${spec.log}`);
  if (!res.ok) throw new Error(`console log ${spec.log}: HTTP ${res.status}`);
  const rows = (await res.text()).split('\n').filter(Boolean).map((l) => JSON.parse(l));
  const chunks = rows.filter((r) => 'data' in r).map((r) => ({ t: r.t * 1000, data: r.data }));
  /* Secrets (the API token) are blanked at the exact ranges recorded during capture; lengths are kept,
   * so chunk boundaries and the terminal layout stay as they were. */
  if (spec.redact?.length) {
    const chars = [...chunks.map((c) => c.data).join('')];
    const joined = chunks.map((c) => c.data).join('');
    if (chars.length !== joined.length) throw new Error('redaction needs one UTF-16 unit per character; this log has astral characters');
    for (const [a, b] of spec.redact) for (let i = a; i < b; i++) chars[i] = '•';
    const red = chars.join('');
    let pos = 0;
    for (const c of chunks) {
      const n = c.data.length;
      c.data = red.slice(pos, pos + n);
      pos += n;
    }
  }
  const host = document.createElement('div');
  host.style.display = 'none';
  host.style.height = '100%';
  $('term-host').append(host);
  const f = plan.format;
  const availW = f.screen.w - 44;
  const availH = f.screen.h - f.termBar - 36;
  const fontSize = Math.floor(Math.min(availW / (spec.cols * 0.6), availH / (spec.rows * 1.22)) * 10) / 10;
  const term = new window.Terminal({
    cols: spec.cols, rows: spec.rows, fontFamily: '"Jig Mono", Consolas, monospace', fontSize, lineHeight: 1.18,
    theme: termTheme(), allowTransparency: true, cursorBlink: false, scrollback: 5000, disableStdin: true,
    convertEol: false, allowProposedApi: true,
  });
  term.open(host);
  /* The row-height ratio above is only an estimate for the font; shrink until every row really fits. */
  host.style.visibility = 'hidden';
  host.style.display = 'block';
  const screen = host.querySelector('.xterm-screen');
  while (screen.getBoundingClientRect().height > availH && term.options.fontSize > 8) {
    term.options.fontSize = Math.round((term.options.fontSize - 0.5) * 10) / 10;
  }
  host.style.display = 'none';
  host.style.visibility = '';
  terms.set(id, { term, host, chunks, idx: 0, src: -Infinity, spec });
}

function writeTerm(t, data) {
  return new Promise((resolve) => t.term.write(data, resolve));
}

async function showTerm(id, src) {
  const t = terms.get(id);
  if (!t) throw new Error(`console session "${id}" was not loaded`);
  if (activeTerm !== t) {
    for (const o of terms.values()) o.host.style.display = 'none';
    t.host.style.display = 'block';
    activeTerm = t;
  }
  if (src < t.src) {
    t.term.reset();
    t.idx = 0;
  }
  let data = '';
  while (t.idx < t.chunks.length && t.chunks[t.idx].t <= src) data += t.chunks[t.idx++].data;
  if (data) await writeTerm(t, data);
  t.src = src;
}

function frameAt(src) {
  const fr = plan.screenFrames;
  let lo = 0;
  let hi = fr.length - 1;
  if (src < fr[0][0]) return fr[0][1];
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (fr[mid][0] <= src) lo = mid;
    else hi = mid - 1;
  }
  return fr[lo][1];
}

function cropAt(shot, local) {
  const ks = shot.crops;
  if (!ks?.length) return { x: 0, y: 0, w: 1920, h: 1080 };
  if (local <= ks[0].at) return ks[0].box;
  for (let i = 1; i < ks.length; i++) {
    if (local < ks[i].at) {
      const a = ks[i - 1];
      const b = ks[i];
      const k = ease(clamp((local - b.at + b.ease) / b.ease, 0, 1));
      if (local < b.at - b.ease) return a.box;
      return { x: a.box.x + (b.box.x - a.box.x) * k, y: a.box.y + (b.box.y - a.box.y) * k,
        w: a.box.w + (b.box.w - a.box.w) * k, h: a.box.h + (b.box.h - a.box.h) * k };
    }
  }
  return ks[ks.length - 1].box;
}

function screenTransform(c) {
  const f = plan.format.screen;
  let s = Math.max(f.w / c.w, f.h / c.h);
  s = Math.max(s, Math.min(f.w / 1920, f.h / 1080));
  let tx = f.w / 2 - (c.x + c.w / 2) * s;
  let ty = f.h / 2 - (c.y + c.h / 2) * s;
  if (1920 * s >= f.w) tx = clamp(tx, f.w - 1920 * s, 0);
  else tx = (f.w - 1920 * s) / 2;
  if (1080 * s >= f.h) ty = clamp(ty, f.h - 1080 * s, 0);
  else ty = (f.h - 1080 * s) / 2;
  return { s, tx, ty };
}

const windowOpacity = (local, a, b, fade = 0.25) => (local < a || local > b ? 0 : Math.min(1, (local - a) / fade, (b - local) / fade));

async function renderFrame(i) {
  const tOut = i / plan.fps;
  const shot = plan.shots.find((s) => tOut >= s.outStart && tOut < s.outStart + s.outDur) || plan.shots[plan.shots.length - 1];
  const local = tOut - shot.outStart;
  const fadeIn = shot.fadeIn ?? FADE;
  const fadeOut = shot.fadeOut ?? FADE;
  const op = clamp(Math.min(fadeIn ? local / fadeIn : 1, fadeOut ? (shot.outDur - local) / fadeOut : 1), 0, 1);
  const src = shot.srcFrom + local * 1000 * (shot.speed || 1);

  $('screen-box').style.opacity = shot.type === 'screen' ? op : 0;
  $('term-box').style.opacity = shot.type === 'term' ? op : 0;
  $('card').style.opacity = shot.type === 'card' ? op : 0;

  if (shot.type === 'screen') {
    const img = $('frame-a');
    const url = `/cap/screen/${frameAt(src)}`;
    if (img.getAttribute('src') !== url) {
      img.src = url;
      await img.decode();
    }
    const tr = screenTransform(cropAt(shot, local));
    $('screen-layer').style.transform = `translate(${tr.tx}px, ${tr.ty}px) scale(${tr.s})`;
    const rings = $('rings');
    rings.replaceChildren(...(shot.rings || []).filter((r) => local >= r.from && local <= r.to).map((r) => {
      const d = document.createElement('div');
      d.className = 'ring';
      const pad = 10;
      Object.assign(d.style, {
        left: `${tr.tx + (r.box.x - pad) * tr.s}px`, top: `${tr.ty + (r.box.y - pad) * tr.s}px`,
        width: `${(r.box.w + 2 * pad) * tr.s}px`, height: `${(r.box.h + 2 * pad) * tr.s}px`,
        opacity: String(windowOpacity(local, r.from, r.to, 0.3)),
      });
      return d;
    }));
  } else if (shot.type === 'term') {
    $('term-title').textContent = shot.termTitle || '';
    await showTerm(shot.session, src);
  } else if (shot.type === 'card') {
    const key = `${shot.outStart}`;
    if (key !== lastCardKey) {
      lastCardKey = key;
      $('card-kicker').textContent = shot.card.kicker || '';
      richText($('card-title'), shot.card.title || '');
      richText($('card-sub'), shot.card.sub || '');
      $('card-lines').replaceChildren(...(shot.card.lines || []).map((l) => {
        const li = document.createElement('li');
        richText(li, l);
        return li;
      }));
      await avatarReady;
      const av = shot.card.avatar || { state: 'idle' };
      $('card-jig').setState(av.state, av.state === 'working' ? { task: av.task, background: Boolean(av.background) } : { background: Boolean(av.background) });
    }
  }

  /* header (portrait), chapter, caption */
  if (plan.format.header) {
    $('header-kicker').textContent = shot.header?.kicker || '';
    richText($('header-title'), shot.header?.title || '');
    $('header').style.opacity = shot.type === 'card' ? 0 : op;
  }
  $('chapter').textContent = shot.chapter || '';
  $('chapter').style.opacity = shot.type === 'card' ? 0 : op;
  const cap = (shot.captions || []).find((c) => local >= c.from && local <= c.to);
  const capEl = $('caption');
  if (cap) {
    if (capEl.dataset.key !== `${shot.outStart}:${cap.from}`) {
      capEl.dataset.key = `${shot.outStart}:${cap.from}`;
      richText(capEl, cap.text);
    }
    capEl.style.opacity = String(Math.min(op, windowOpacity(local, cap.from, cap.to)));
  } else {
    capEl.style.opacity = '0';
  }

  const speed = $('speed');
  speed.textContent = shot.speed && shot.speed !== 1 ? `Sped up ×${shot.speed}` : '';
  speed.style.opacity = shot.speed && shot.speed !== 1 && shot.type !== 'card' ? String(op) : '0';
  const skip = $('skip');
  skip.textContent = shot.skipLabel || '';
  skip.style.opacity = shot.skipLabel ? String(Math.min(op, windowOpacity(local, 0, 3.2))) : '0';

  const call = (shot.callouts || []).find((c) => local >= c.from && local <= c.to);
  const callEl = $('callout');
  if (call) {
    callEl.replaceChildren(Object.assign(document.createElement('b'), { textContent: call.label }), document.createTextNode(call.text));
    callEl.style.opacity = String(Math.min(op, windowOpacity(local, call.from, call.to)));
  } else callEl.style.opacity = '0';

  $('watermark').textContent = plan.watermark || '';
  $('watermark').style.opacity = plan.watermark ? '1' : '0';

  /* advance the deterministic clock: two 60 Hz sub-steps per 30 fps frame */
  window.__clock.step(1000 / 60);
  window.__clock.step(1000 / 60);
  return { shot: shot.id, type: shot.type, src: Math.round(src) };
}

window.__demo = {
  async load(p) {
    plan = p;
    const root = document.documentElement;
    for (const [k, v] of Object.entries(p.vars)) root.style.setProperty(k, v);
    await document.fonts.load('20px "Jig Mono"');
    await document.fonts.load('600 20px "Jig Sans"');
    await document.fonts.load('600 20px "Jig Label"');
    await document.fonts.ready;
    avatarReady = import(`/avatar/jig-avatar.js`).then(() => customElements.whenDefined('jig-avatar'));
    await avatarReady;
    const avatarTheme = cssVar('--avatar-theme');
    if (avatarTheme) $('card-jig').setAttribute('theme', avatarTheme);
    for (const [id, spec] of Object.entries(p.terms)) await loadTerm(id, spec);
    return { frames: p.totalFrames };
  },
  renderFrame,
};
