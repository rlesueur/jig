/*
 * The small part of Markdown that models write in replies: headings, paragraphs, bullet and numbered lists,
 * tables, fenced code, rules, and **bold**, *italic*, `code`, [links](https://...) and bare web addresses.
 *
 * parseMarkdown() is pure (text in, plain objects out), so it can be tested without a browser.
 * renderMarkdown() builds DOM nodes from that with createElement and text nodes only: nothing the model
 * writes is ever parsed as HTML. Links are only made for http(s) addresses, and a link always shows its
 * full address, so a reply can't hide where a link goes behind friendly text.
 */

const FENCE = /^\s*```/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const BULLET = /^(\s*)[-*+]\s+(.*)$/;
const NUMBERED = /^(\s*)(\d{1,9})[.)]\s+(.*)$/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

const cells = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());

/** Block structure of a reply: [{type: 'heading'|'para'|'list'|'table'|'code'|'rule', ...}]. */
export function parseMarkdown(text) {
  const lines = String(text ?? '').replace(/\r\n?/g, '\n').split('\n');
  const blocks = [];
  let para = [];
  const endPara = () => {
    if (para.length) blocks.push({ type: 'para', inline: parseInline(para.join('\n')) });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (FENCE.test(line)) {
      endPara();
      const body = [];
      for (i += 1; i < lines.length && !FENCE.test(lines[i]); i++) body.push(lines[i]);
      blocks.push({ type: 'code', text: body.join('\n') });
      continue;
    }
    if (!line.trim()) { endPara(); continue; }
    const h = line.match(HEADING);
    if (h) { endPara(); blocks.push({ type: 'heading', level: h[1].length, inline: parseInline(h[2]) }); continue; }
    if (RULE.test(line)) { endPara(); blocks.push({ type: 'rule' }); continue; }
    if (TABLE_ROW.test(line) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      endPara();
      const head = cells(line).map(parseInline);
      const rows = [];
      for (i += 2; i < lines.length && TABLE_ROW.test(lines[i]); i++) rows.push(cells(lines[i]).map(parseInline));
      i -= 1;
      blocks.push({ type: 'table', head, rows });
      continue;
    }
    const b = line.match(BULLET);
    const n = line.match(NUMBERED);
    if (b || n) {
      endPara();
      const ordered = !b;
      const start = ordered ? Number(n[2]) : 1;
      const items = [];
      for (; i < lines.length; i++) {
        const mb = lines[i].match(BULLET);
        const mn = lines[i].match(NUMBERED);
        const m = ordered ? mn : mb;
        if (m) {
          items.push({ depth: Math.min(Math.floor(m[1].replace(/\t/g, '  ').length / 2), 3), text: ordered ? m[3] : m[2] });
        } else if ((mb || mn) && items.length) {
          /* a nested list of the other kind: keep it as an indented item */
          const mm = mb || mn;
          items.push({ depth: Math.min(Math.floor(mm[1].replace(/\t/g, '  ').length / 2), 3) || 1, text: mb ? mb[2] : mn[3] });
        } else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) {
          items[items.length - 1].text += `\n${lines[i].trim()}`;
        } else {
          break;
        }
      }
      i -= 1;
      blocks.push({ type: 'list', ordered, start, items: items.map((it) => ({ depth: it.depth, inline: parseInline(it.text) })) });
      continue;
    }
    para.push(line);
  }
  endPara();
  return blocks;
}

const SAFE_URL = /^https?:\/\/[^\s<>"']+$/i;
/* trailing punctuation that ends a sentence rather than an address */
const trimUrl = (u) => {
  let url = u;
  while (/[.,;:!?)\]]$/.test(url)) {
    if (url.endsWith(')') && (url.match(/\(/g) || []).length >= (url.match(/\)/g) || []).length) break;
    url = url.slice(0, -1);
  }
  return url;
};

/** Inline runs: [{t: 'text'|'strong'|'em'|'code'|'link'|'br', ...}]. */
export function parseInline(text) {
  const out = [];
  const s = String(text ?? '');
  const push = (node) => {
    const last = out[out.length - 1];
    if (node.t === 'text' && last?.t === 'text') last.text += node.text;
    else if (node.t !== 'text' || node.text) out.push(node);
  };
  let i = 0;
  let plain = '';
  const flush = () => { if (plain) push({ t: 'text', text: plain }); plain = ''; };
  while (i < s.length) {
    const rest = s.slice(i);
    let m;
    if (s[i] === '\n') { flush(); out.push({ t: 'br' }); i += 1; continue; }
    if (s[i] === '\\' && /[\\`*_[\]()#+\-.!|]/.test(s[i + 1] || '')) { plain += s[i + 1]; i += 2; continue; }
    if ((m = rest.match(/^`([^`\n]+)`/))) { flush(); push({ t: 'code', text: m[1] }); i += m[0].length; continue; }
    if ((m = rest.match(/^(\*\*|__)(?=\S)([\s\S]*?\S)\1/))) { flush(); push({ t: 'strong', children: parseInline(m[2]) }); i += m[0].length; continue; }
    if ((m = rest.match(/^(\*|_)(?=\S)([^*_\n]*?\S)\1(?![*_])/)) && !(m[1] === '_' && /\w/.test(s[i - 1] || ''))) {
      flush(); push({ t: 'em', children: parseInline(m[2]) }); i += m[0].length; continue;
    }
    if ((m = rest.match(/^\[([^\]\n]+)\]\(\s*<?([^)\s>]+)>?\s*\)/)) && SAFE_URL.test(m[2])) {
      flush(); push({ t: 'link', text: m[1], url: m[2] }); i += m[0].length; continue;
    }
    if ((m = rest.match(/^https?:\/\/[^\s<>"'`]+/i)) && !/[\w/]/.test(s[i - 1] || '')) {
      const url = trimUrl(m[0]);
      flush(); push({ t: 'link', text: url, url }); i += url.length; continue;
    }
    plain += s[i];
    i += 1;
  }
  flush();
  return out;
}

function inlineNodes(doc, runs) {
  const nodes = [];
  for (const r of runs) {
    if (r.t === 'text') nodes.push(doc.createTextNode(r.text));
    else if (r.t === 'br') nodes.push(doc.createElement('br'));
    else if (r.t === 'code') {
      const c = doc.createElement('code');
      c.textContent = r.text;
      nodes.push(c);
    } else if (r.t === 'strong' || r.t === 'em') {
      const e = doc.createElement(r.t);
      e.append(...inlineNodes(doc, r.children));
      nodes.push(e);
    } else if (r.t === 'link') {
      const a = doc.createElement('a');
      a.href = r.url;
      a.textContent = r.url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      if (r.text !== r.url) nodes.push(doc.createTextNode(`${r.text} (`), a, doc.createTextNode(')'));
      else nodes.push(a);
    }
  }
  return nodes;
}

/** DOM nodes for a reply, built without ever parsing the text as HTML. */
export function renderMarkdown(text, doc = document) {
  const make = (tag, children = []) => {
    const e = doc.createElement(tag);
    e.append(...children);
    return e;
  };
  return parseMarkdown(text).map((b) => {
    if (b.type === 'para') return make('p', inlineNodes(doc, b.inline));
    if (b.type === 'heading') {
      const h = make('p', [make('strong', inlineNodes(doc, b.inline))]);
      h.className = `md-h md-h${b.level}`;
      return h;
    }
    if (b.type === 'rule') return make('hr');
    if (b.type === 'code') {
      const pre = make('pre', [make('code')]);
      pre.firstChild.textContent = b.text;
      return pre;
    }
    if (b.type === 'table') {
      const row = (cellsIn, tag) => make('tr', cellsIn.map((c) => make(tag, inlineNodes(doc, c))));
      const table = make('table', [make('thead', [row(b.head, 'th')]), make('tbody', b.rows.map((r) => row(r, 'td')))]);
      const wrap = make('div', [table]);
      wrap.className = 'md-table';
      return wrap;
    }
    const list = make(b.ordered ? 'ol' : 'ul', b.items.map((it) => {
      const li = make('li', inlineNodes(doc, it.inline));
      if (it.depth) li.dataset.depth = String(it.depth);
      return li;
    }));
    if (b.ordered && b.start !== 1) list.start = b.start;
    return list;
  });
}
