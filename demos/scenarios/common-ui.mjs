/* UI steps several scenarios share: sign in with the real token, Settings navigation, and union boxes for framing. */
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
  await waitUntil('the UI to connect to /events', async () => /Live/.test(await ui.connection(p).textContent()));
  ctx.checks.ok('signed in through the UI with the token from `jig token show`', !(await ui.loginDialog(p).isVisible()));
  await ui.chatView(p).waitFor({ state: 'visible' });
}

/** The smallest box (frame pixels) containing all the given boxes. */
export function union(...boxes) {
  const x = Math.min(...boxes.map((b) => b.x));
  const y = Math.min(...boxes.map((b) => b.y));
  const r = Math.max(...boxes.map((b) => b.x + b.w));
  const bt = Math.max(...boxes.map((b) => b.y + b.h));
  return { x, y, w: r - x, h: bt - y };
}

/** Open Settings with the gear button (if not already open), then the given section from the side list. */
export async function openSettings(s, section) {
  const p = s.page;
  if (!(await ui.settingsSection(p, 'model').isVisible()) && !(await p.getByTestId('settings').isVisible())) {
    await s.click(ui.openSettings(p));
    await p.getByTestId('settings').waitFor({ state: 'visible' });
  }
  await s.click(ui.settingsNav(p, section));
  await ui.settingsSection(p, section).waitFor({ state: 'visible' });
}

/** Back to the conversation from Settings. */
export async function backToChat(s) {
  const p = s.page;
  await s.click(ui.closeSettings(p));
  await ui.chatView(p).waitFor({ state: 'visible' });
}
