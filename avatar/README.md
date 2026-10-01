# Jig avatar

An animated, dependency-free web component for Jig, the always-on personal agent that runs only on local models. The mascot is drawn live as a vector rig on a single `<canvas>`. Its head, horns, blinking eyes, mouth, arms, ribbon tail, prism shards, sparks and vortex are separate parts, so every state can move them independently. Nothing is a pre-rendered raster.

`assets/jig-mascot-reference.jpg` is the approved artwork the rig is matched against.

## Run the demo

```powershell
cd jig\avatar
python -m http.server 8765 --bind 127.0.0.1
```

Open <http://127.0.0.1:8765/>. You can deep-link to a state with query parameters, for example `?state=working&task=coding`, `?state=working&task=browsing&background=1` or `?state=talking&audio=0.6`.

## Usage

```html
<script type="module" src="./jig-avatar.js"></script>

<jig-avatar state="idle" style="width: 320px; height: 320px"></jig-avatar>
<jig-avatar state="working" task="browsing" shape="circle" style="width: 48px; height: 48px"></jig-avatar>
```

```js
import { STATES, TASKS } from './jig-avatar.js';

const jig = document.querySelector('jig-avatar');
jig.setState('thinking');
jig.setState('working', { task: 'writing' });
jig.setState('working', { task: 'browsing', background: true }); // dimmed: read-only background research
jig.setState('talking');
jig.setAudioLevel(0.42); // call every frame or on each audio chunk while on a call
jig.addEventListener('jig-statechange', (e) => console.log(e.detail)); // { state, task, previous, background }
```

### API

| Member | Description |
| --- | --- |
| `setState(name, { task, background })` | Moves smoothly to a new state. `task` is required for `working` and not allowed for any other state. `background` is optional (default `false`) and works with any state. |
| `setAudioLevel(level)` | Sets the live voice level, from 0 to 1, used by `talking`. |
| `state`, `task`, `background`, `audioLevel` | Read-only getters. |
| `jig-statechange` event | Fires on every change, with `detail: { state, task, previous, background }`. |
| `STATES`, `TASKS` | Exported arrays of the valid names. |

Invalid input throws instead of guessing. An unknown state, an unknown task, a missing task for `working`, a `background` value that is not a boolean, or an audio level outside 0–1 raises a `RangeError` or `TypeError`.

### Background work

`background: true` marks the state as background work, such as read-only proactive research. The animation stays the same (for example the browsing panes and scan sweep), but the rig dims, the eyes go half-lidded, and the motion softens and slows. The change is interpolated like any other state change, so switching the flag on or off cross-fades. Each `setState` call describes the whole state, so leaving `background` out sets it back to `false`.

The `background` option is separate from the `background` state alias, which still means `monitoring`.

### Attributes

| Attribute | Values |
| --- | --- |
| `state`, `task` | Mirror `setState` and stay in sync with it. |
| `background` | Boolean attribute that mirrors the `background` option. Present (or any value other than `false`) means background work. |
| `audio-level` | 0–1. |
| `shape` | `rounded` (default), `circle` for avatars and icons, or `none` for a transparent background. |
| `framing` | `auto` (default; switches to `icon` below 110px), `icon` (head-and-shoulders crop with reduced detail), or `full`. |
| `reduced-motion` | Forces reduced motion. Otherwise the component follows `prefers-reduced-motion`. |

Size the element with CSS. The canvas follows it through a `ResizeObserver` and renders at the device pixel ratio, capped at 2.

## States

Every pose parameter is interpolated, so changing state never produces a hard cut. The prisms morph between layouts by blending their vertices, and task overlays fade in as their weight rises.

| State | Animation |
| --- | --- |
| `idle` (alias `resting`) | Gentle bob and breathing, slow ribbon sway, occasional blinks, tumbling glass shards drifting slowly. |
| `monitoring` (aliases `sleeping`, `background`) | Dimmed with eyes closed, a slow cyan pulse, a radar sweep and sonar rings. An eye occasionally half-opens and glances around. This is read-only proactive research. |
| `thinking` | Swirl and orbit speed up, eyes look upwards, a hand goes up to the head, sparks form a halo and thought bubbles rise. |
| `working` + `browsing` | Shards become browser panes facing Jig, with a scan sweep. Eyes jump between panes and a reading beam follows them. |
| `working` + `writing` | A page and an envelope appear. An amber ribbon from the hand writes looping cursive lines, then the envelope flashes as "sent". |
| `working` + `coding` | A terminal pane types coloured code with a blinking caret. Sparks turn into rising code glyphs, and hands tap in a typing rhythm. |
| `working` + `shopping` | Jig holds a payment card with a shimmer and a confirmation tick, while £ coins spin on a ring around the tail. |
| `working` + `scheduling` | Shards become a calendar page of day tiles, with a ticking highlight and a booked tick. A clock ring with a sweeping hand circles the head. |
| `talking` (aliases `call`, `on-call`) | The mouth opens with the audio level, ribbons widen and ripple, sound arcs radiate and the vortex brightens. |
| `approval` (alias `needs-approval`) | A cute wave with a curling hand, a bigger bounce, an amber tint, amber pulse rings and an alert bubble. |
| `success` | A one-off pirouette (horizontal spin), a confetti burst and happy ^ ^ eyes with both arms up. |
| `error` (alias `blocked`) | Droop: the head sinks and tilts, arms hang, the tail narrows, eyes are sad and lidded, colours turn cool blue, shards sink and a small × and teardrop appear. |
| `paused` | Motion slows almost to a stop, colours desaturate and a pause badge appears. |

The state set follows how Meta Muse and OpenAI Dots present always-on agents. Both keep working in the background, return when they need a decision or approval (for example before sending or buying), run proactive read-only research, support calls, and can be paused. Jig adds per-task working variants so the user can tell at a glance what kind of work is under way.

## Mapping agent runtime events to states

Drive the avatar from your runtime's event stream. Keep a single source of truth, and call `setState` only when the derived state changes.

| Runtime event | Avatar call |
| --- | --- |
| Agent ready with nothing queued | `setState('idle')` |
| Scheduled watchers or proactive research running with read-only tools only | `setState('monitoring')` |
| Model generating a plan or reasoning, before any tool call | `setState('thinking')` |
| Tool call: web search, fetch, browser navigation or page reading | `setState('working', { task: 'browsing' })` |
| Proactive read-only research fetching or reading a source | `setState('working', { task: 'browsing', background: true })` |
| Tool call: drafting or sending email, messages or documents | `setState('working', { task: 'writing' })` |
| Tool call: shell, code editing, running tests | `setState('working', { task: 'coding' })` |
| Tool call: basket, checkout or payment | `setState('working', { task: 'shopping' })` |
| Tool call: calendar read or write, booking, reminders | `setState('working', { task: 'scheduling' })` |
| Voice session open | `setState('talking')`, then `setAudioLevel(rms)` from the active speaker's audio (Jig's output while it speaks, the user's input while it listens) |
| Action review requires the user (approve, hand off, sign in, connect an app) | `setState('approval')` |
| Task completed successfully | `setState('success')`, then return to `idle` or `monitoring` after about 2–3 seconds |
| Task failed, permission denied, or blocked by a rule | `setState('error')` until the user acknowledges it or the task retries |
| User paused the agent or a task | `setState('paused')` |

Precedence when several things are true at once, highest first: `approval`, `error`, `talking`, `working`, `thinking`, `paused`, background `working`, `monitoring`, `idle`. A pending approval should always win, because it is the one state where Jig needs the user. When parallel tool calls span several task kinds, show the one the user is most likely to care about, or the most recently started.

## Performance and accessibility

- All instances share one `requestAnimationFrame` loop. Off-screen instances are skipped through `IntersectionObserver`, and no layout is read inside the loop.
- The neon glow is built from layered strokes with additive blending rather than `shadowBlur`. In the demo, the large avatar costs about 0.7 ms of JavaScript per frame, and all 13 avatars together about 4 ms.
- Icon framing reduces detail: fewer ribbons, sparks and vortex rings, with lighter glow passes.
- With reduced motion, amplitudes shrink to about 15%, phases slow down, and the pirouette and confetti are skipped. States still cross-fade quickly.
- The element sets `role="img"` and an `aria-label` such as "Jig is working: coding", or "Jig is working: browsing, in the background" for background work.

## Files

- `jig-avatar.js`: the web component and rig.
- `index.html`: the demo page, with state and task buttons, a background-work toggle, an audio slider, a real microphone input and 48px icons.
- `assets/jig-mascot-reference.jpg`: the approved mascot artwork.
- `screenshots/`: captured states.
