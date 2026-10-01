import './jig-avatar.js';

const LABELS = {
  idle: 'Idle',
  monitoring: 'Monitoring: read-only research',
  thinking: 'Thinking',
  approval: 'Needs your approval',
  success: 'Done',
  error: 'Blocked',
  paused: 'Paused',
};

const avatar = document.getElementById('hero-avatar');
const label = document.getElementById('state-label');
const backgroundToggle = document.getElementById('background-toggle');
const buttons = [...document.querySelectorAll('.switcher button')];
let current = buttons.find((b) => b.getAttribute('aria-pressed') === 'true');

function describe({ state, task, background }) {
  const base = state === 'working' ? `Working: ${task}` : LABELS[state];
  if (base === undefined) throw new RangeError(`No label for avatar state "${state}"`);
  return background ? `${base}, in the background` : base;
}

function apply(button) {
  const { state, task } = button.dataset;
  const background = backgroundToggle.checked;
  avatar.setState(state, state === 'working' ? { task, background } : { background });
  current.setAttribute('aria-pressed', 'false');
  button.setAttribute('aria-pressed', 'true');
  current = button;
}

avatar.addEventListener('jig-statechange', (e) => {
  label.textContent = describe(e.detail);
  document.querySelector('.stage').dataset.state = e.detail.state;
});

for (const button of buttons) button.addEventListener('click', () => apply(button));
backgroundToggle.addEventListener('change', () => apply(current));

const video = document.getElementById('promo');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

let userPaused = false;
let autoPausing = false;
video.addEventListener('pause', () => { if (!autoPausing) userPaused = true; autoPausing = false; });
video.addEventListener('play', () => { userPaused = false; });

new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (entry.isIntersecting && !reducedMotion.matches && !userPaused) {
      video.play().catch((err) => console.warn('Promo video did not autoplay:', err.message));
    } else if (!entry.isIntersecting && !video.paused) {
      autoPausing = true;
      video.pause();
    }
  }
}, { threshold: 0.4 }).observe(video);
