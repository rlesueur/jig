/*
 * Light or dark, chosen before the first paint so the page never flashes the wrong colours.
 * "system" follows prefers-color-scheme live; "light" and "dark" are explicit overrides saved on this device.
 * A saved value that cannot be read or is not recognised is reported on screen by app.js (jigAppearance.problem).
 */
(function () {
  const KEY = 'jig.appearance';
  const CHOICES = ['system', 'light', 'dark'];
  const root = document.documentElement;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let choice = 'system';
  let problem = null;

  try {
    const saved = window.localStorage.getItem(KEY);
    if (saved !== null) {
      if (CHOICES.includes(saved)) choice = saved;
      else problem = `The saved appearance "${saved}" isn't one Jig knows, so it is following your system for now. Choose Light, Dark or Follow my system in Settings, Appearance.`;
    }
  } catch (err) {
    problem = `Jig couldn't read your saved appearance (${err.message}), so it is following your system for now.`;
  }

  const resolve = () => (choice === 'system' ? (media.matches ? 'dark' : 'light') : choice);

  function apply() {
    const theme = resolve();
    root.dataset.theme = theme;
    root.dataset.appearance = choice;
    root.style.colorScheme = theme;
    document.dispatchEvent(new CustomEvent('jig-themechange', { detail: { theme, choice } }));
  }

  media.addEventListener('change', () => { if (choice === 'system') apply(); });

  window.jigAppearance = {
    get choice() { return choice; },
    get theme() { return resolve(); },
    get problem() { return problem; },
    /** Save and apply a choice; throws if it is not one of the three, or if it cannot be saved. */
    set(next) {
      if (!CHOICES.includes(next)) throw new RangeError(`unknown appearance "${next}"`);
      window.localStorage.setItem(KEY, next);
      choice = next;
      problem = null;
      apply();
    },
  };

  apply();
})();
