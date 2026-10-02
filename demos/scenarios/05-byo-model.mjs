/*
 * Demo 5: Bring your own model. The example profiles and the config in a real console, then the real
 * capability check: `jig health` passing against the configured server (tool calling, structured output
 * and an image), and failing, loudly and with the reason, against a port where nothing is listening.
 */
import path from 'node:path';
import { fit, marksOf, until } from '../lib/edit.mjs';
import { DEMOS, PORTS, requirePortFree, snapshotDir } from '../lib/util.mjs';
import { parseJsonBlock } from './common.mjs';

const PROFILES = "Select-String -Path profiles\\*.toml -Pattern '^base_url'";
const CONFIG = 'Get-Content $env:JIG_CONFIG -TotalCount 11';
const BAD = 'jig --config ..\\..\\configs\\nothing-listening.toml health';

export default {
  title: 'Bring your own model',

  async capture(ctx) {
    const { checks } = ctx;
    await requirePortFree(PORTS.nothing);
    checks.ok(`nothing is listening on port ${PORTS.nothing}`, true);
    const src = snapshotDir();
    checks.equal('the bad config is the one under demos/configs', path.resolve(src, '..\\..\\configs\\nothing-listening.toml'),
      path.join(DEMOS, 'configs', 'nothing-listening.toml'));

    const sh = await ctx.term('host', { cwd: src, title: 'Windows PowerShell · Jig repository' });
    const prof = await sh.typeCommand(PROFILES);
    for (const f of ['ollama.toml', 'lmstudio.toml', 'vllm.toml']) checks.ok(`profiles list ${f}`, prof.output.includes(f), prof.output);
    const cfg = await sh.typeCommand(CONFIG);
    checks.ok('the config points at the local server on 8080', /base_url = "http:\/\/127\.0\.0\.1:8080\/v1"/.test(cfg.output), cfg.output);

    const good = await sh.typeCommand('jig health', { timeoutMs: 240000 });
    const report = parseJsonBlock(good.output);
    checks.ok('jig health: agent tool calling passed', report.agent?.tool_calling === true, report.agent);
    checks.ok('jig health: agent structured output passed', report.agent?.structured_output === true, report.agent);
    checks.ok('jig health: vision (a real image) passed', report.agent?.vision === true, report.agent);
    checks.ok('jig health: Sentinel structured output passed', report.sentinel?.structured_output === true, report.sentinel);

    await requirePortFree(PORTS.nothing);
    const bad = await sh.typeCommand(BAD, { expectCode: null, timeoutMs: 120000 });
    checks.ok('jig health fails against the empty port (non-zero exit)', bad.code !== 0, `exit ${bad.code}`);
    checks.ok('the failure names the endpoint', bad.output.includes(`127.0.0.1:${PORTS.nothing}`), bad.output);
    ctx.note(`failure output: ${bad.output.trim().split('\n').slice(-4).join(' | ')}`);
  },

  edit(m) {
    const k = marksOf(m);
    const prof = k.cmd('host', PROFILES);
    const cfg = k.cmd('host', CONFIG);
    const good = k.cmd('host', 'jig health');
    const bad = k.cmd('host', BAD);
    const T = 'Windows PowerShell · Jig repository';
    return {
      shots: [
        { type: 'card', dur: 3.4, card: { kicker: 'Jig · demo', title: 'Bring your own model', sub: 'Any local server with an OpenAI-compatible API and tool calling.', avatar: { state: 'idle' } } },
        { type: 'term', session: 'host', title: T, chapter: '1 · Pick a server', from: prof.start - 300, to: until(cfg.end + 3000, good),
          captions: [
            { at: prof.start, dur: 5.5, text: 'Example profiles for common local servers: Ollama, LM Studio, vLLM, llama.cpp.' },
            { at: cfg.start, text: 'The config is just an endpoint. This demo uses a llama.cpp server on port 8080.' },
          ] },
        { type: 'term', session: 'host', title: T, chapter: '2 · Check it really works', from: good.start - 300, to: until(good.end + 3000, bad),
          speed: fit(good.start, until(good.end + 3000, bad), 12),
          captions: [
            { at: good.start, dur: 5, text: '`jig health` does not trust the model card: it *tests* the server.' },
            { at: good.enter + 1500, text: 'A real tool call, a JSON-schema answer and an image. All passed.' },
          ] },
        { type: 'term', session: 'host', title: T, chapter: '3 · And when it does not', from: bad.start - 300, to: bad.end + 3500,
          sfx: [{ at: bad.end, kind: 'block' }],
          captions: [
            { at: bad.start, dur: 4.5, text: 'Now point it at a port where nothing is listening.' },
            { at: bad.end - 200, text: 'It fails *loudly*, says why, and never quietly falls back to another model.' },
          ] },
        { type: 'card', dur: 3.8, card: { title: '*Jig*', sub: 'Your model, your machine. Open source, Apache-2.0.', avatar: { state: 'idle' } } },
      ],
    };
  },
};
