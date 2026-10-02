/*
 * A cloudflared quick tunnel to the local test site, run in its own (off-camera) console and stopped at
 * the end of the capture. It gives the page a real public https URL, so the real web_fetch can reach it.
 */
import path from 'node:path';
import { TermSession } from './term.mjs';
import { WORK, requireFile, waitUntil } from './util.mjs';

export const CLOUDFLARED = path.join(WORK, 'bin', 'cloudflared.exe');

export async function openTunnel(ctx, localUrl) {
  requireFile(CLOUDFLARED, 'run demos/run.ps1 safety, which downloads cloudflared and checks its SHA-256');
  const t = await ctx.term('tunnel', { argv: [CLOUDFLARED, 'tunnel', '--no-autoupdate', '--url', localUrl], cols: 160, rows: 40, title: 'cloudflared' });
  const m = await t.expect(/https:\/\/[a-z0-9-]+\.trycloudflare\.com/, { timeoutMs: 60000, what: 'the quick tunnel URL' });
  const url = m[0];
  await waitUntil(`${url} to answer through Cloudflare`, async () => {
    try { return (await fetch(`${url}/health`)).ok; } catch { return false; }
  }, { timeoutMs: 120000, intervalMs: 2000 });
  ctx.onCleanup('cloudflared tunnel', async () => { await t.kill(); });
  return url;
}
