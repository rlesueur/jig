/*
 * The work view: what Jig did for one reply, from the live tool events of its run (tool.start, tool.summary,
 * tool.end, approval.requested/resolved; see jig/work.py for the summaries).
 *
 * Progressive disclosure: Jig's corner shows one friendly line and, once tests have run, a pass/fail meter.
 * "Show the steps" lists each step in plain words; each step opens to the command with every test's result,
 * the code, or the file's changes before and after. Everything here is built from text nodes, never HTML,
 * and kept only in this page: the summaries are not stored anywhere else.
 */

let h = null; // helpers from app.js: el, plural, doing(tool) -> "running a command"

export function initWork(helpers) {
  h = helpers;
}

const base = (p) => String(p ?? '').split(/[\\/]/).pop() || String(p ?? '');
const cap = (s) => (s ? `${s[0].toUpperCase()}${s.slice(1)}` : s);
const listWords = (names) => (names.length <= 1 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`);

/* What a finished step did, for tools without a summary of their own. */
const DONE_WORDS = {
  web_fetch: 'Read a web page', browser_open: 'Opened a web page', browser_read: 'Read a web page',
  browser_screenshot: 'Looked at a web page', browser_click: 'Clicked on a web page', browser_type: 'Typed into a web page',
  browser_fill: 'Filled in a form', browser_submit: 'Submitted a form', note_write: 'Wrote a note',
  note_list: 'Looked at my notes', memory_search: 'Checked what I remember', memory_add: 'Remembered something',
  memory_forget: 'Forgot something', current_time: 'Checked the time', schedule_create: 'Set up a schedule',
  schedule_list: 'Looked at the schedules',
};

function testsOf(s) {
  return s && s.tests ? s.tests : null;
}

export class Work {
  constructor({ runId, ask }) {
    this.runId = runId;
    this.ask = ask; // what you asked, for the steps' heading
    this.steps = [];
    this.open = new Set(); // ids of steps whose detail is shown
    this.status = 'running';
  }

  /** Steps as the list shows them, with reads in a row as one. */
  get size() {
    return grouped(this.steps).length;
  }

  /** The latest test run's counts, or null. */
  get tests() {
    for (let i = this.steps.length - 1; i >= 0; i -= 1) {
      const t = testsOf(this.steps[i].summary);
      if (t) return t;
    }
    return null;
  }

  get current() {
    return [...this.steps].reverse().find((s) => s.state === 'now' || s.state === 'asking') || null;
  }

  /** Feed one event from the run; returns true if anything changed. */
  handle(ev) {
    const d = ev.data;
    if (ev.type === 'approval.requested') {
      if (this.steps.some((s) => s.approvalId === d.approval_id)) return false;
      this.steps.push({ id: d.approval_id, tool: d.tool, state: 'asking', approvalId: d.approval_id, args: d.args || {}, summary: null });
      return true;
    }
    if (ev.type === 'approval.resolved') {
      const step = this.steps.find((s) => s.approvalId === d.approval_id);
      if (!step) return false;
      step.state = d.status === 'approved' ? 'approved' : 'denied';
      step.answer = d.status;
      return true;
    }
    if (ev.type === 'tool.start') {
      const asked = this.steps.find((s) => s.state === 'approved' && s.tool === d.tool && !s.callId);
      if (asked) {
        asked.callId = d.call_id;
        asked.state = 'now';
      } else {
        this.steps.push({ id: d.call_id, callId: d.call_id, tool: d.tool, state: 'now', args: {}, summary: null });
      }
      return true;
    }
    const step = this.steps.find((s) => s.callId && s.callId === d.call_id);
    if (!step) return false;
    if (ev.type === 'tool.summary') {
      step.summary = d.summary;
      return true;
    }
    if (ev.type === 'tool.end') {
      step.state = d.ok ? 'done' : 'failed';
      return true;
    }
    return false;
  }

  finish(status) {
    this.status = status;
    for (const s of this.steps) {
      if (s.state === 'now' || s.state === 'asking' || s.state === 'approved') s.state = status === 'done' ? 'done' : 'stopped';
    }
  }

  /* ---------- words ---------- */

  /** What Jig is doing right now, as a caption ("Running the tests…"), or null between steps. */
  nowWords() {
    const s = this.current;
    if (!s) return null;
    if (s.state === 'asking') return 'I need your OK to carry on.';
    return `${cap(presentWords(s))}\u2026`;
  }

  /** The one friendly line in Jig's corner. */
  line() {
    const t = this.tests;
    const bad = t ? t.failed + t.errors : 0;
    if (this.status === 'running') {
      const now = this.current;
      if (now && now.state === 'asking') return 'I need your OK to carry on.';
      if (t && bad) return `${h.plural(bad, 'test')} failed, so I\u2019m working on ${bad === 1 ? 'it' : 'them'}.`;
      if (now) return `${cap(presentWords(now))}\u2026`;
      if (t && t.passed) return `All ${h.plural(t.passed, 'test')} pass. Nearly there\u2026`;
      return `Working on it: ${h.plural(this.doneCount, 'step')} so far.`;
    }
    if (this.status !== 'done') return this.status === 'failed' ? 'I couldn\u2019t finish this one.' : 'Stopped.';
    if (t && bad) return `Done, but ${h.plural(bad, 'test')} still ${bad === 1 ? 'fails' : 'fail'}.`;
    if (t) return `All done. ${t.passed === 1 ? 'The test passes' : `All ${t.passed} tests pass`}.`;
    return `All done, in ${h.plural(this.doneCount, 'step')}.`;
  }

  get doneCount() {
    const finished = (s) => s.state === 'done' || s.state === 'failed';
    return grouped(this.steps).filter((r) => (r.group ? r.group.every(finished) : finished(r.step))).length;
  }

  /* ---------- the corner: one line, a meter, and the way in ---------- */

  summaryNode({ stepsOpen, onToggle }) {
    const { el } = h;
    const t = this.tests;
    const running = this.status === 'running';
    const parts = [el('p', { class: 'work-line', 'data-testid': 'work-line' },
      running ? el('span', { class: 'pulse', 'aria-hidden': 'true' }) : el('span', { class: `work-mark ${this.status === 'done' && !(t && t.failed + t.errors) ? 'ok' : 'bad'}`, 'aria-hidden': 'true', text: this.status === 'done' && !(t && t.failed + t.errors) ? '\u2713' : '!' }),
      el('span', { text: this.line() }))];
    if (t) parts.push(meter(t));
    if (this.steps.length) {
      parts.push(el('button', {
        type: 'button', class: 'btn btn-small work-more', 'aria-expanded': String(stepsOpen), 'aria-controls': 'steps-pane',
        'data-testid': 'work-steps-toggle', onclick: onToggle,
      }, stepsOpen ? 'Hide the steps' : 'Show the steps',
      el('span', { class: 'count', text: running ? `${this.size} so far` : String(this.size) })));
    }
    return parts;
  }

  /* ---------- the steps ---------- */

  stepsNodes({ onRender }) {
    const { el } = h;
    const items = [];
    const rows = grouped(this.steps);
    for (const row of rows) items.push(row.group ? lookGroup(row.group, this, onRender) : stepItem(row.step, this, onRender));
    return [
      el('div', { class: 'steps-head' },
        el('h2', { class: 'steps-title', id: `steps-title-${this.runId}`, text: this.status === 'running' ? 'What I\u2019m doing' : 'What I did' }),
        this.ask ? el('p', { class: 'hint-quiet steps-ask', text: `For: ${this.ask.length > 120 ? `${this.ask.slice(0, 119)}\u2026` : this.ask}` }) : null),
      el('ol', { class: 'steps-list', 'aria-labelledby': `steps-title-${this.runId}`, 'data-testid': 'steps-list' }, items),
    ];
  }
}

function presentWords(step) {
  const s = step.summary;
  const path = (s && s.path) || step.args.path;
  if (step.tool === 'write_file' && path) return `${s && s.created ? 'making' : 'changing'} ${base(path)}`;
  if (step.tool === 'read_file' && path) return `reading ${base(path)}`;
  if ((step.tool === 'run_command' || step.tool === 'run_python') && looksLikeTests(step.args.command || step.args.code)) return 'running the tests';
  return h.doing(step.tool);
}

const looksLikeTests = (text) => /\b(pytest|unittest|npm (run )?test|cargo test|go test|jest|vitest|mocha)\b/.test(String(text || ''));

/* Reading and listing files in a row is one step: "Looked at your files". */
function grouped(steps) {
  const rows = [];
  for (const step of steps) {
    const look = step.summary && (step.summary.kind === 'read' || step.summary.kind === 'list') && !step.summary.error && step.state === 'done';
    const last = rows[rows.length - 1];
    if (look && last && last.group) last.group.push(step);
    else if (look && last && last.look) rows[rows.length - 1] = { group: [last.step, step] };
    else rows.push({ step, look });
  }
  return rows;
}

function mark(state) {
  const { el } = h;
  if (state === 'now') return el('span', { class: 'step-mark' }, el('span', { class: 'spin', 'aria-hidden': 'true' }), el('span', { class: 'sr-only', text: 'Doing now: ' }));
  if (state === 'asking' || state === 'approved') return el('span', { class: 'step-mark', 'aria-hidden': 'true', text: '?' });
  const sign = { done: '\u2713', failed: '!', denied: '\u2013', stopped: '\u2013' }[state] || '\u2022';
  const words = { done: 'Done: ', failed: 'Went wrong: ', denied: 'Not done: ', stopped: 'Stopped: ' }[state] || '';
  return el('span', { class: 'step-mark' }, el('span', { 'aria-hidden': 'true', text: sign }), el('span', { class: 'sr-only', text: words }));
}

function title(step) {
  const s = step.summary;
  const doing = h.doing(step.tool);
  if (step.state === 'asking') return `Asking you before ${doing}`;
  if (step.state === 'approved') return `You said yes to ${doing}`;
  if (step.state === 'denied') return `You said no, so I didn\u2019t carry on ${doing}`;
  if (step.state === 'now') return `${cap(presentWords(step))}\u2026`;
  if (s && !s.error) {
    if (s.kind === 'read') return `Read ${base(s.path)}`;
    if (s.kind === 'list') return `Looked through ${s.path === '.' ? 'my workspace' : base(s.path)}`;
    if (s.kind === 'write') return `${s.created ? 'Made' : 'Changed'} ${base(s.path)}`;
    if (s.kind === 'command') return s.tests ? 'Ran the tests' : 'Ran a command';
    if (s.kind === 'code') return s.tests ? 'Ran the tests' : 'Ran some Python';
  }
  if (step.state === 'failed' || (s && s.error)) return `Something went wrong ${doing}`;
  if (step.state === 'stopped') return `Stopped ${doing}`;
  return DONE_WORDS[step.tool] || cap(`finished ${doing}`);
}

function sub(step) {
  const { el, plural } = h;
  const s = step.summary;
  if (!s) return null;
  if (s.error) return el('p', { class: 'step-sub error-text', text: s.error });
  if (s.kind === 'read') return el('p', { class: 'step-sub', text: `${plural(s.chars, 'character')}${s.truncated ? ', the first part of it' : ''}` });
  if (s.kind === 'list') return el('p', { class: 'step-sub', text: s.count ? `${plural(s.count, 'thing')} in it` : 'Nothing in it yet' });
  if (s.kind === 'write') {
    if (s.too_big) return el('p', { class: 'step-sub', text: 'A big file, so the changes aren\u2019t shown here' });
    if (s.created) return el('p', { class: 'step-sub', text: `A new file, ${plural(s.added, 'line')}` });
    return el('p', { class: 'step-sub', text: s.added || s.removed ? `${plural(s.added, 'line')} added, ${s.removed} removed` : 'No lines changed' });
  }
  const t = s.tests;
  if (t) {
    return el('p', { class: 'step-sub pills' },
      el('span', { class: 'pill ok', text: `${t.passed} passed` }),
      t.failed ? el('span', { class: 'pill bad', text: `${t.failed} failed` }) : null,
      t.errors ? el('span', { class: 'pill bad', text: `${plural(t.errors, 'error')}` }) : null,
      t.skipped ? el('span', { class: 'pill', text: `${t.skipped} skipped` }) : null);
  }
  if (s.timed_out) return el('p', { class: 'step-sub error-text', text: 'It took too long, so it was stopped' });
  return el('p', { class: s.exit_code === 0 ? 'step-sub' : 'step-sub error-text', text: s.exit_code === 0 ? 'Finished without a problem' : `Ended with a problem (exit code ${s.exit_code})` });
}

function hasDetail(step) {
  return Boolean((step.summary && !step.summary.error) || step.args.command || step.args.code);
}

function toggle(id, work, onRender, label) {
  const shown = work.open.has(id);
  return h.el('button', {
    type: 'button', class: 'step-toggle', 'aria-expanded': String(shown), 'aria-controls': `step-detail-${id}`,
    'aria-label': `${shown ? 'Hide' : 'Show'} the details: ${label}`, 'data-testid': 'step-toggle',
    onclick: () => {
      if (work.open.has(id)) work.open.delete(id);
      else work.open.add(id);
      onRender(id);
    },
  }, shown ? 'Hide' : 'Show');
}

function stepItem(step, work, onRender) {
  const { el } = h;
  const words = title(step);
  const detailed = hasDetail(step) && step.state !== 'asking';
  const shown = detailed && work.open.has(step.id);
  return el('li', { class: `step ${step.state}`, 'data-testid': 'step', dataset: { tool: step.tool, state: step.state, id: step.id } },
    mark(step.state),
    el('div', { class: 'step-body' },
      el('div', { class: 'step-top' }, el('p', { class: 'step-title', text: words }), detailed ? toggle(step.id, work, onRender, words) : null),
      sub(step),
      shown ? el('div', { class: 'step-detail', id: `step-detail-${step.id}`, 'data-testid': 'step-detail' }, detail(step)) : null));
}

function lookGroup(steps, work, onRender) {
  const { el } = h;
  const id = steps[0].id;
  const names = [...new Set(steps.map((s) => base(s.summary.path)).filter((n) => n && n !== '.'))];
  const shown = work.open.has(id);
  const words = 'Looked at the files';
  return el('li', { class: 'step done', 'data-testid': 'step', dataset: { tool: 'files', state: 'done', id } },
    mark('done'),
    el('div', { class: 'step-body' },
      el('div', { class: 'step-top' }, el('p', { class: 'step-title', text: words }), toggle(id, work, onRender, words)),
      el('p', { class: 'step-sub', text: names.length ? listWords(names.slice(0, 6)) + (names.length > 6 ? ` and ${names.length - 6} more` : '') : 'My workspace' }),
      shown ? el('div', { class: 'step-detail', id: `step-detail-${id}`, 'data-testid': 'step-detail' },
        el('ul', { class: 'looked' }, steps.map((s) => el('li', {},
          el('code', { text: s.summary.path }), ` ${s.summary.kind === 'read' ? `read, ${h.plural(s.summary.chars, 'character')}` : `listed, ${h.plural(s.summary.count, 'thing')}`}`)))) : null));
}

/* ---------- one step, opened ---------- */

function detail(step) {
  const { el } = h;
  const s = step.summary;
  const parts = [];
  if (step.tool === 'run_command' || (s && s.kind === 'command')) {
    parts.push(el('div', { class: 'step-cmd' }, el('p', { class: 'step-cmd-label', text: 'The command' }),
      el('code', { class: 'step-cmd-line', 'data-testid': 'step-command', text: s ? s.command : String(step.args.command || '') })));
  } else if (step.tool === 'run_python' || (s && s.kind === 'code')) {
    const lines = s ? s.code : String(step.args.code || '').split('\n').slice(0, 40);
    const more = s ? s.code_lines - s.code.length : 0;
    parts.push(el('div', { class: 'step-cmd' }, el('p', { class: 'step-cmd-label', text: 'The code' }),
      el('pre', { class: 'step-cmd-line', 'data-testid': 'step-code', text: lines.join('\n') + (more > 0 ? `\n\u2026 and ${h.plural(more, 'more line')}` : '') })));
  }
  if (!s) return parts;
  if (s.kind === 'command' || s.kind === 'code') {
    if (s.tests) parts.push(testResults(s.tests));
    if (s.tail.length) {
      parts.push(el('details', { class: 'output', open: !s.tests && s.exit_code !== 0 ? '' : null },
        el('summary', { text: s.output_lines > s.tail.length ? `What it printed (the last ${s.tail.length} of ${s.output_lines} lines)` : 'What it printed' }),
        el('pre', { class: 'output-text', 'data-testid': 'step-output', text: s.tail.join('\n') })));
    } else {
      parts.push(el('p', { class: 'hint-quiet', text: 'It didn\u2019t print anything.' }));
    }
    parts.push(el('p', { class: 'hint-quiet', 'data-testid': 'step-exit', text: s.timed_out ? 'Stopped: it took too long.' : `Exit code ${s.exit_code}${s.exit_code === 0 ? ', which means it finished without a problem.' : ', which means something went wrong.'}` }));
  } else if (s.kind === 'write') {
    parts.push(diffView(s));
  } else if (s.kind === 'list') {
    parts.push(el('ul', { class: 'looked' }, s.names.map((n) => el('li', {}, el('code', { text: n })))),
      s.count > s.names.length ? el('p', { class: 'hint-quiet', text: `And ${s.count - s.names.length} more.` }) : null);
  } else if (s.kind === 'read') {
    parts.push(el('p', { class: 'hint-quiet' }, 'Read ', el('code', { text: s.path }), `: ${h.plural(s.chars, 'character')}${s.truncated ? ', the first part of a longer file' : ''}.`));
  }
  return parts;
}

function meter(t) {
  const { el } = h;
  const bad = t.failed + t.errors;
  const words = `${t.passed} passed${bad ? `, ${bad} to fix` : ''}${t.skipped ? `, ${t.skipped} skipped` : ''}`;
  return el('div', { class: 'meter-row', 'data-testid': 'work-meter' },
    el('div', { class: 'meter', role: 'img', 'aria-label': `Tests: ${words}` },
      t.passed ? el('span', { class: 'm-ok', style: `flex: ${t.passed}` }) : null,
      bad ? el('span', { class: 'm-bad', style: `flex: ${bad}` }) : null,
      t.skipped ? el('span', { class: 'm-skip', style: `flex: ${t.skipped}` }) : null),
    el('p', { class: 'meter-text', 'aria-hidden': 'true' },
      el('span', { class: 'pass', text: `${t.passed} passed` }), bad ? ' \u00b7 ' : '', bad ? el('span', { class: 'fail', text: `${bad} to fix` }) : null));
}

const OUTCOME = { passed: ['\u2713', 'ok', 'passed'], failed: ['\u2715', 'bad', 'failed'], error: ['\u2715', 'bad', 'error'], skipped: ['\u2013', 'skip', 'skipped'] };

function testResults(t) {
  const { el, plural } = h;
  const bad = t.failed + t.errors;
  const head = el('p', { class: 'step-cmd-label', text: `${plural(t.passed + bad + t.skipped, 'test')}: ${t.passed} passed${bad ? `, ${bad} failed` : ''}${t.skipped ? `, ${t.skipped} skipped` : ''}` });
  if (!t.results.length) {
    return el('div', { class: 'step-cmd' }, head, el('p', { class: 'hint-quiet', text: 'The test runner didn\u2019t list each test by name.' }));
  }
  const order = { failed: 0, error: 0, passed: 1, skipped: 2 };
  const rows = [...t.results].sort((a, b) => order[a.outcome] - order[b.outcome]);
  return el('div', { class: 'step-cmd' }, head,
    el('ul', { class: 'results', 'data-testid': 'test-results' }, rows.map((r) => {
      const [sign, cls, word] = OUTCOME[r.outcome];
      const parts = r.name.split(/::|\./);
      return el('li', { class: `r ${cls}`, 'data-testid': 'test-result', dataset: { outcome: r.outcome } },
        el('span', { class: 'r-mark', 'aria-hidden': 'true', text: sign }),
        el('span', { class: 'r-name', text: parts[parts.length - 1] }, el('span', { class: 'sr-only', text: `: ${word}` })),
        el('span', { class: 'r-id', text: r.name }));
    })));
}

function diffView(s) {
  const { el } = h;
  if (s.too_big) return el('p', { class: 'hint-quiet', text: 'This file is too big to show its changes here.' });
  const before = [];
  const after = [];
  s.diff.forEach((hunk, i) => {
    if (i > 0) {
      before.push(gap());
      after.push(gap());
    }
    for (const [op, oldNo, newNo, text] of hunk.lines) {
      if (op !== '+') before.push(line(op === '-' ? 'del' : 'same', oldNo, text));
      if (op !== '-') after.push(line(op === '+' ? 'add' : 'same', newNo, text));
    }
  });
  const col = (cls, head, rows, label) => el('section', { class: `diff-col ${cls}`, 'aria-label': label },
    el('p', { class: 'diff-head' }, el('span', { class: 'dh-dot', 'aria-hidden': 'true' }), head),
    el('pre', { class: 'diff-lines' }, rows.length ? rows : el('span', { class: 'dl same' }, el('span', { class: 'dn' }), el('span', { class: 'dm' }), el('span', { class: 'dt muted-inline', text: '(empty)' }))));
  const cols = s.created
    ? [col('after', `New file: ${s.path}`, after, `The new file ${s.path}`)]
    : [col('before', 'Before', before, `${s.path} before`), col('after', 'After', after, `${s.path} after`)];
  return el('div', { class: 'diff-wrap' },
    el('div', { class: `diff${s.created ? ' single' : ''}`, 'data-testid': 'step-diff' }, cols),
    s.cut ? el('p', { class: 'hint-quiet', text: 'There are more changes than fit here; this shows the first part.' }) : null);
}

function line(kind, no, text) {
  const { el } = h;
  const sign = kind === 'add' ? '+' : kind === 'del' ? '\u2212' : ' ';
  return el('span', { class: `dl ${kind}`, 'data-testid': kind === 'same' ? null : `diff-${kind}` },
    el('span', { class: 'dn', 'aria-hidden': 'true', text: no ? String(no) : '' }),
    el('span', { class: 'dm', 'aria-hidden': 'true', text: sign }),
    kind === 'same' ? null : el('span', { class: 'sr-only', text: kind === 'add' ? 'Added: ' : 'Removed: ' }),
    el('span', { class: 'dt', text: text || ' ' }));
}

function gap() {
  return h.el('span', { class: 'dl gap', 'aria-hidden': 'true' }, h.el('span', { class: 'dn' }), h.el('span', { class: 'dm' }), h.el('span', { class: 'dt', text: '\u22ef' }));
}
