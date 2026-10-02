/* Tests for jig/web/markdown.js (run by tests/test_web_markdown.py, or directly with `node --test tests/web`). */
import assert from 'node:assert/strict';
import test from 'node:test';
import { parseInline, parseMarkdown } from '../../jig/web/markdown.js';

const flat = (runs) => runs.map((r) => (r.children ? `<${r.t}>${flat(r.children)}</${r.t}>` : r.t === 'text' ? r.text
  : r.t === 'link' ? `[${r.text}|${r.url}]` : r.t === 'br' ? '\n' : `<${r.t}>${r.text}</${r.t}>`)).join('');

test('bold, italic, code and links inline', () => {
  assert.equal(flat(parseInline('**Price:** £35 for *one* year, see `railcards.md`')),
    '<strong>Price:</strong> £35 for <em>one</em> year, see <code>railcards.md</code>');
  assert.equal(flat(parseInline('[Two Together](https://www.railcard.co.uk/two-together-railcard/) and more')),
    '[Two Together|https://www.railcard.co.uk/two-together-railcard/] and more');
  assert.equal(flat(parseInline('Source: https://www.gov.uk/bank-holidays.json.')),
    'Source: [https://www.gov.uk/bank-holidays.json|https://www.gov.uk/bank-holidays.json].');
});

test('only http(s) addresses become links', () => {
  assert.equal(flat(parseInline('[click](javascript:alert(1))')), '[click](javascript:alert(1))');
  assert.equal(flat(parseInline('[file](file:///C:/secret.txt)')), '[file](file:///C:/secret.txt)');
});

test('arithmetic and snake_case are not emphasis', () => {
  assert.equal(flat(parseInline('2 * 3 * 4 = 24')), '2 * 3 * 4 = 24');
  assert.equal(flat(parseInline('totals_by_shop and summary_line')), 'totals_by_shop and summary_line');
});

test('nothing is ever HTML: tags stay text', () => {
  assert.equal(flat(parseInline('<img src=x onerror=alert(1)> **ok**')), '<img src=x onerror=alert(1)> <strong>ok</strong>');
});

test('blocks: headings, lists, paragraphs, code and tables', () => {
  const blocks = parseMarkdown([
    '## Family & Friends Railcard',
    '- **Price:** £35',
    '- Who: up to 4 adults',
    '  and 4 children',
    '',
    '1. Read the pages',
    '2. Save the report',
    '',
    'Plain paragraph',
    'second line',
    '',
    '```',
    'python3 -m unittest -v',
    '```',
    '| Card | Price |',
    '| --- | ---: |',
    '| 16-25 | £35 |',
    '---',
  ].join('\n'));
  assert.deepEqual(blocks.map((b) => b.type), ['heading', 'list', 'list', 'para', 'code', 'table', 'rule']);
  assert.equal(blocks[0].level, 2);
  assert.equal(blocks[1].ordered, false);
  assert.equal(blocks[1].items.length, 2);
  assert.equal(flat(blocks[1].items[1].inline), 'Who: up to 4 adults\nand 4 children');
  assert.equal(blocks[2].ordered, true);
  assert.equal(blocks[2].items.length, 2);
  assert.equal(flat(blocks[3].inline), 'Plain paragraph\nsecond line');
  assert.equal(blocks[4].text, 'python3 -m unittest -v');
  assert.deepEqual(blocks[5].head.map(flat), ['Card', 'Price']);
  assert.deepEqual(blocks[5].rows.map((r) => r.map(flat)), [['16-25', '£35']]);
});

test('an unfinished reply (still streaming) parses without losing text', () => {
  const blocks = parseMarkdown('Here is **the answ');
  assert.equal(flat(blocks[0].inline), 'Here is **the answ');
  const code = parseMarkdown('```\nprint(1)');
  assert.equal(code[0].text, 'print(1)');
});
