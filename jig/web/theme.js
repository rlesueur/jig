// Look of the web UI. Loaded as a classic script in <head> so the theme is applied before the first paint
// (the CSP forbids inline scripts). The choice comes from ?theme=, then the saved choice, then the default,
// and is stored per browser in localStorage. Every theme styles the same live UI; only CSS changes.
(() => {
  const THEMES = {
    neon: 'Classic neon',
    cosy: 'Cosy evening',
    sunny: 'Sunny studio',
    dance: 'Dance hall',
  };
  const DEFAULT = 'neon';
  const KEY = 'jig.theme';
  const root = document.documentElement;
  const problems = [];

  function saved() {
    try {
      return localStorage.getItem(KEY);
    } catch (err) {
      problems.push(`Your theme choice can't be remembered in this browser (${err.message}).`);
      return null;
    }
  }

  function apply(name, remember) {
    root.dataset.theme = name;
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.content = name === 'sunny' ? 'light' : 'dark';
    if (!remember) return;
    try {
      localStorage.setItem(KEY, name);
    } catch (err) {
      problems.push(`Your theme choice can't be remembered in this browser (${err.message}).`);
    }
  }

  const asked = new URLSearchParams(location.search).get('theme');
  const stored = saved();
  if (asked && !(asked in THEMES)) {
    problems.push(`There is no theme called "${asked}". The themes are: ${Object.keys(THEMES).join(', ')}.`);
  }
  if (stored && !(stored in THEMES)) problems.push(`The saved theme "${stored}" no longer exists.`);
  const initial = asked in THEMES ? asked : stored in THEMES ? stored : DEFAULT;
  apply(initial, asked in THEMES);

  document.addEventListener('DOMContentLoaded', () => {
    const select = document.getElementById('theme-select');
    select.replaceChildren(...Object.entries(THEMES).map(([value, label]) => new Option(label, value)));
    select.value = root.dataset.theme;
    select.addEventListener('change', () => apply(select.value, true));
    const box = document.getElementById('errors');
    for (const text of problems) {
      const toast = document.createElement('div');
      toast.className = 'toast';
      const body = document.createElement('div');
      body.className = 'toast-body';
      const title = document.createElement('p');
      title.className = 'toast-title';
      title.textContent = 'A small problem with the theme';
      const detail = document.createElement('p');
      detail.className = 'toast-detail';
      detail.textContent = text;
      body.append(title, detail);
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = 'Dismiss';
      b.addEventListener('click', () => toast.remove());
      toast.append(body, b);
      box.append(toast);
    }
  });
})();
