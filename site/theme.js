/*
 * Light or dark, chosen before the first paint so the page never flashes the wrong colours. It follows
 * prefers-color-scheme until the visitor picks one with the theme button, and that choice is kept on this
 * device. Loaded as a classic script in <head>; main.js wires up the button.
 */
(function () {
  const KEY = 'jig.site.theme';
  const root = document.documentElement;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let saved = null;
  try {
    const value = window.localStorage.getItem(KEY);
    if (value === 'light' || value === 'dark') saved = value;
  } catch (err) {
    console.warn('Jig site: could not read the saved theme, following the system theme:', err.message);
  }

  const resolve = () => saved || (media.matches ? 'dark' : 'light');

  function apply() {
    const theme = resolve();
    root.dataset.theme = theme;
    document.dispatchEvent(new CustomEvent('jig-themechange', { detail: { theme } }));
  }

  media.addEventListener('change', () => { if (!saved) apply(); });

  window.jigSiteTheme = {
    get theme() { return resolve(); },
    /** Pick 'light' or 'dark' and keep it on this device. Throws if it cannot be saved. */
    set(next) {
      if (next !== 'light' && next !== 'dark') throw new RangeError(`unknown theme "${next}"`);
      window.localStorage.setItem(KEY, next);
      saved = next;
      apply();
    },
  };

  apply();
})();
