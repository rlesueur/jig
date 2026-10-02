/* UI steps several scenarios share: sign in with the real token, and union boxes for framing. */
import { BASE } from '../lib/jig.mjs';
import { ui } from '../lib/ui-map.mjs';
import { waitUntil } from '../lib/util.mjs';

/** Open the real UI and sign in with the token from `jig token show`, through the UI's own dialog. */
export async function signIn(ctx, s) {
  const p = s.page;
  await s.goto(`${BASE}/`);
  await ui.loginDialog(p).waitFor({ state: 'visible' });
  await s.paste(ui.tokenField(p), ctx.jig.token);
  await s.click(ui.signIn(p));
  await waitUntil('the UI to connect to /events', async () => /Live/.test(await ui.connection(p).innerText()));
  ctx.checks.ok('signed in through the UI with the token from `jig token show`', !(await ui.loginDialog(p).isVisible()));
}

/** The smallest box (frame pixels) containing all the given boxes. */
export function union(...boxes) {
  const x = Math.min(...boxes.map((b) => b.x));
  const y = Math.min(...boxes.map((b) => b.y));
  const r = Math.max(...boxes.map((b) => b.x + b.w));
  const bt = Math.max(...boxes.map((b) => b.y + b.h));
  return { x, y, w: r - x, h: bt - y };
}

export async function openTab(s, name) {
  await s.click(ui.tab(s.page, name));
  await ui.panel(s.page, name).waitFor({ state: 'visible' });
}
